from typing import Union

from tqdm import tqdm
from datetime import datetime, timedelta
import traceback
import re
import json
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import lxml.html as LH
from http.cookies import SimpleCookie
import urllib
import easyocr
import pandas as pd
import time

import concurrent.futures
import urllib3

from court_codes import COURT_CODES_ALL
from utility import get_headers, get_new_date_range, extract_pdf_fragment, get_tracking_data, save_court_tracking_date, \
    get_json_file, get_pdf_output_path, is_pdf_downloaded, default_search_payload, generate_date_tuples

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

reader = easyocr.Reader(["en"])

root_url = "https://judgments.ecourts.gov.in"
output_dir = Path("./data")


pdf_link_payload = "val=0&lang_flg=undefined&path=cnrorders/taphc/orders/2017/HBHC010262202017_1_2047-06-29.pdf#page=&search=+&citation_year=&fcourt_type=2&file_type=undefined&nc_display=undefined&ajax_req=true&app_token=c64944b84c687f501f9692e239e2a0ab007eabab497697f359a2f62e4fcd3d10"

page_size = 1000
NO_CAPTCHA_BATCH_SIZE = 25
MAX_WORKERS = 1


def start_download(downloader):
    downloader.download_all()
    return downloader


# from BatchDownloader import BatchDownloader
class Downloader:
    def __init__(self, court_code_to_process, start_date, end_date, thread_no):
        self.thread_no = thread_no
        self.start_date = start_date
        self.end_date = end_date
        self.root_url = "https://judgments.ecourts.gov.in"
        self.search_url = f"{self.root_url}/pdfsearch/?p=pdf_search/home/"
        self.captcha_url = f"{self.root_url}/pdfsearch/vendor/securimage/securimage_show.php"  # not lint skip/
        self.captcha_token_url = f"{self.root_url}/pdfsearch/?p=pdf_search/checkCaptcha"
        self.pdf_link_url = f"{self.root_url}/pdfsearch/?p=pdf_search/openpdfcaptcha"
        self.pdf_link_url_wo_captcha = f"{root_url}/pdfsearch/?p=pdf_search/openpdf"

        self.court_code = court_code_to_process
        self.tracking_data = get_tracking_data(with_lock=True)
        self.court_codes = COURT_CODES_ALL
        self.court_name = self.court_codes[self.court_code]
        # self.court_tracking = self.tracking_data.get(self.court_code, {})
        self.court_tracking = {}
        self.session_cookie_name = "PHPSESSID"
        self.ecourts_token_cookie_name = "JSESSION"
        self.session_id = None
        self.ecourts_token = None
        self.app_token = (
            "490a7e9b99e4553980213a8b86b3235abc51612b038dbdb1f9aa706b633bbd6c"
        )
        self.docs_found_so_far = 0
        self.pages_found_so_far = 0
        self.pdf_already_exists = 0
        self.pdf_fresh_downloaded = 0
        self.full_download_refresh = 0

    def download(self):
        try:
            self.process_court()
        except Exception as e:
            print(e)
            traceback.print_exc()
            print("Error processing court in download() method", self.court_code, self.court_name)

    def divide_list(self, lst, n=24):
        """
        Divides a list into n approximately equal sublists.

        :param lst: List to be divided
        :param n: Number of sublists
        :return: A list of n sublists
        """
        if n <= 0:
            raise ValueError("The number of sublists (n) must be greater than 0.")

        k, m = divmod(len(lst), n)
        return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]

    def process_court(self):
        # last_date = self.court_tracking.get("last_date", "2023-12-31")
        from_date, to_date = self.start_date, self.end_date
        if from_date is None:
            print("No more data to download for: ", self.court_code, self.court_name)
            return
        search_payload = default_search_payload()
        search_payload["from_date"] = from_date
        search_payload["to_date"] = to_date
        self.init_user_session()
        search_payload["state_code"] = self.court_code
        search_payload["app_token"] = self.app_token
        results_available = True

        while results_available:
            try:
                print("performing search.")
                response = self.request_api("POST", self.search_url, search_payload)
                res_dict = response.json()
                if (
                        "reportrow" in res_dict
                        and "aaData" in res_dict["reportrow"]
                        and len(res_dict["reportrow"]["aaData"]) > 0
                ):
                    no_of_results = len(res_dict["reportrow"]["aaData"])
                    if search_payload["sEcho"] != self.pages_found_so_far:
                        self.pages_found_so_far = search_payload["sEcho"]
                        self.docs_found_so_far += no_of_results

                    print("Found results", no_of_results, from_date, to_date)
                    results_to_download = res_dict["reportrow"]["aaData"]
                    # results_to_download = results_to_download[1:3]
                    downloading_workload = self.divide_list(results_to_download)
                    from BatchDownloader import BatchDownloader
                    downloader_list = []
                    for single_workload in downloading_workload:
                        # self.refresh_token()
                        batch_download = BatchDownloader(single_workload, output_dir, self.court_code, self.court_name,
                                                         self.ecourts_token_cookie_name, self.app_token,
                                                         self.session_id, self.session_cookie_name, self.ecourts_token, self.thread_no)
                        batch_download.download_all()
                        self.pdf_already_exists += batch_download.pdf_already_exists
                        self.pdf_fresh_downloaded += batch_download.pdf_fresh_downloaded

                    # with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    #     futures = [executor.submit(start_download, downloader) for downloader in downloader_list]
                    #     concurrent.futures.wait(futures)

                    # prepare next iteration
                    search_payload["sEcho"] += 1
                    search_payload["iDisplayStart"] += page_size
                    print("Next iteration: ", search_payload["iDisplayStart"])
                else:
                    print("downloaded completed for ", self.court_code, self.court_name, self.start_date, self.end_date)
                    self.court_tracking["from_date"] = from_date
                    self.court_tracking["to_date"] = to_date
                    self.court_tracking["docs_to_download"] = self.docs_found_so_far
                    self.court_tracking["pdf_already_exists"] = self.pdf_already_exists
                    self.court_tracking["pdf_fresh_downloaded"] = self.pdf_fresh_downloaded
                    self.court_tracking["full_download_refresh"] = self.full_download_refresh
                    self.court_tracking["total_pages"] = self.pages_found_so_far
                    save_court_tracking_date(self.court_code, self.court_tracking)
                    break
                    # last_date = to_date
                    # self.court_tracking["last_date"] = last_date
                    # save_court_tracking_date(self.court_code, self.court_tracking)
                    # from_date, to_date = get_new_date_range(to_date)
                    # if from_date is None:
                    #     print(
                    #         "No more data to download for: ",
                    #         self.court_code,
                    #         self.court_name,
                    #     )
                    #     results_available = False
                    # else:
                    #     search_payload["from_date"] = from_date
                    #     search_payload["to_date"] = to_date
                    #     search_payload["sEcho"] = 1
                    #     search_payload["iDisplayStart"] = 0
                    #     search_payload["iDisplayLength"] = page_size
                    #     print(
                    #         f"Downloading data for: {self.court_code}, court: {self.court_name}, from: {from_date},to: {to_date}"
                    #     )

            except Exception as e:
                print("Error when looping of days/pages and downloading", e)
                if "Invalid Captcha" in str(e):
                    print("Initializing new session")
                    self.init_user_session()
                    search_payload["state_code"] = self.court_code
                    search_payload["app_token"] = self.app_token
                    print("session initialized and searching again.")
                # self.court_tracking["failed_dates"] = self.court_tracking.get(
                #     "failed_dates", []
                # )
                # if from_date not in self.court_tracking["failed_dates"]:
                #     self.court_tracking["failed_dates"].append(
                #         from_date
                #     )  # TODO: should be all the dates from from_date to to_date in case step date > 1
                self.full_download_refresh += 1
                self.court_tracking["full_download_refresh"] = self.full_download_refresh
                self.court_tracking["from_date"] = from_date
                self.court_tracking["to_date"] = to_date
                self.court_tracking["docs_to_download"] = self.docs_found_so_far
                self.court_tracking["pdf_already_exists"] = self.pdf_already_exists
                self.court_tracking["pdf_fresh_downloaded"] = self.pdf_fresh_downloaded
                self.court_tracking["full_download_refresh"] = self.full_download_refresh
                self.court_tracking["total_pages"] = self.pages_found_so_far
                save_court_tracking_date(self.court_code, self.court_tracking)
                print("tracking data saved.")

    # def update_headers_with_new_session(self, headers):
    #     cookie = SimpleCookie()
    #     cookie.load(headers["Cookie"])
    #     cookie[self.session_cookie_name] = self.session_id
    #     headers["Cookie"] = cookie.output(header="", sep=";").strip()

    def solve_captcha(self, retries=0, captcha_url=None):
        if captcha_url is None:
            captcha_url = self.captcha_url
        # download captch image and save
        time.sleep(5 * retries)
        captcha_response = requests.get(
            captcha_url, headers={"Cookie": self.get_cookie()}, verify=False
        )
        captcha_filename = f"/tmp/captcha{self.court_code}-{self.thread_no}.png"
        with open(captcha_filename, "wb") as f:
            f.write(captcha_response.content)
        result = reader.readtext(captcha_filename)
        Path(captcha_filename).unlink()
        captch_text = result[0][1].strip()
        # strip, remove any special characters present anywhere
        # final text should be 6 characters long
        captch_text = "".join([c for c in captch_text if c.isnumeric()])
        if len(captch_text) != 6:
            if retries > 5:
                raise Exception("Captcha not solved")
            return self.solve_captcha(retries + 1)
        return captch_text

    def solve_pdf_download_captcha(self, response, pdf_link_payload, retries=0):
        """
        example response: <div class="col-md-auto p-0 ms-3"><img style="padding-right: 0px;border:1px solid #ccc;" id="captcha_image_pdf" src="/pdfsearch/vendor/securimage/securimage_show.php?630074f111af510939c620c884aba1ca" alt="CAPTCHA"   tabindex="0" width="120"/><div id="captcha_image_pdf_audio_div" style="display:inline"><audio id="captcha_image_pdf_audio" preload="none" style="display: none"><source id="captcha_image_pdf_source_wav" src="/pdfsearch/vendor/securimage/securimage_play.php?id=66026662d9542" type="audio/wav"><object type="application/x-shockwave-flash" data="/pdfsearch/vendor/securimage/securimage_play.swf?bgcol=%23ffffff&amp;icon_file=%2Fpdfsearch%2Fvendor%2Fsecurimage%2Fimages%2Fspeaker-btn.png&amp;audio_file=%2Fpdfsearch%2Fvendor%2Fsecurimage%2Fsecurimage_play.php%3F" height="32" width="32"><param name="movie" value="/pdfsearch/vendor/securimage/securimage_play.swf?bgcol=%23ffffff&amp;icon_file=%2Fpdfsearch%2Fvendor%2Fsecurimage%2Fimages%2Fspeaker-btn.png&amp;audio_file=%2Fpdfsearch%2Fvendor%2Fsecurimage%2Fsecurimage_play.php%3F" /></object></audio></div><div id="captcha_image_pdf_audio_controls"  style="display:inline-block;"><a tabindex="0" class="captcha_play_button" href="/pdfsearch/vendor/securimage/securimage_play.php?id=66026662d9551" onclick="return false"><img class="captcha_play_image" height="32" width="32" src="/pdfsearch/vendor/securimage/images/speaker-btn.png" alt="Play CAPTCHA Audio" style="border: 0px"><img class="captcha_loading_image rotating" height="32" width="32" src="/pdfsearch/vendor/securimage/images/loading.png" alt="Loading audio" style="display: none"></a><noscript>Enable Javascript for audio controls</noscript></div><script src="/pdfsearch/vendor/securimage/securimage.js"></script><script>captcha_image_pdf_audioObj = new SecurimageAudio({ audioElement: 'captcha_image_pdf_audio', controlsElement: 'captcha_image_pdf_audio_controls' });</script><a tabindex="0" style="border: 0" href="#" title="Refresh Image" onclick="if (typeof window.captcha_image_pdf_audioObj !== 'undefined') captcha_image_pdf_audioObj.refresh(); document.getElementById('captcha_image_pdf').src = '/pdfsearch/vendor/securimage/securimage_show.php?' + Math.random(); this.blur(); return false"><img height="32" width="32" src="/pdfsearch/vendor/securimage/images/refresh-btn.png" alt="Refresh Image" onclick="clearCaptchaText();" style="border: 0px;" /></a></div> <div class="col-md-4"><input maxlength="6" type="text" name="captchapdf" id="captchapdf" class="captchaClass form-control form-control-sm" placeholder="Enter captcha"  tabindex="0"/></div>
        """

        # parse html
        html_str = response["filename"]
        html = LH.fromstring(html_str)
        img_src = html.xpath("//img[@id='captcha_image_pdf']/@src")[0]
        img_src = root_url + img_src
        # download captch image and save
        time.sleep(5 * retries)
        try:
            captcha_text = self.solve_captcha(captcha_url=img_src)
            pdf_link_payload["captcha1"] = captcha_text
            pdf_link_payload["app_token"] = response["app_token"]
            pdf_link_response = self.request_api(
                "POST", self.pdf_link_url_wo_captcha, pdf_link_payload
            )
            res_json = pdf_link_response.json()
            if "message" in res_json and res_json["message"] == "Captcha not solved":
                print("Captcha not solved", pdf_link_response.json())
                if retries == 2:
                    return res_json
                print("Retrying pdf captch solve")
                return self.solve_pdf_download_captcha(
                    response, pdf_link_payload, retries + 1
                )
            return pdf_link_response
        except Exception as e:
            error_when_using_captcha = True
            print("Error when using captcha, retrying captcha solver", e)
            return self.solve_pdf_download_captcha(
                response, pdf_link_payload, retries + 1
            )

    def refresh_token(self, with_app_token=False):
        # print("Current session id ", self.session_id)
        # print("Current token ", self.app_token)
        captcha_text = self.solve_captcha()
        # print("Captcha text ", captcha_text)
        captcha_check_payload = {
            "captcha": captcha_text,
            "search_opt": "PHRASE",
            "ajax_req": "true",
            # "app_token": app_token,
        }
        if with_app_token:
            captcha_check_payload["app_token"] = self.app_token
        res = requests.request(
            "POST",
            self.captcha_token_url,
            headers=get_headers(self.get_cookie(), self.root_url),
            data=captcha_check_payload,
            verify=False,
        )
        res_json = res.json()
        self.app_token = res_json["app_token"]
        self.update_session_id(res)
        print("Refreshed token")
        # print("new session id ", self.session_id)
        # print("new token ", self.app_token)


    def request_api(self, method, url, payload, **kwargs):
        headers = get_headers(self.get_cookie(), self.root_url)
        response = requests.request(
            method,
            url,
            headers=headers,
            data=payload,
            **kwargs,
            timeout=10,
            verify=False,
        )
        # if response is json
        try:
            response_dict = response.json()
        except Exception as e:
            response_dict = {}
        if "app_token" in response_dict:
            self.app_token = response_dict["app_token"]
        self.update_session_id(response)
        if url == self.captcha_token_url:
            return response

        if (
                "filename" in response_dict
                and "securimage_show" in response_dict["filename"]
        ):
            self.app_token = response_dict["app_token"]
            return self.solve_pdf_download_captcha(response_dict, payload)

        elif response_dict.get("session_expire") == "Y" or "errormsg" in response_dict:
            self.refresh_token()
            if payload:
                payload["app_token"] = self.app_token
            return self.request_api(method, url, payload, **kwargs)

        return response

    def get_search_url(self):
        return f"{self.root_url}/pdfsearch/?p=pdf_search/home/"



    def default_pdf_link_payload(self):
        pdf_link_payload_o = urllib.parse.parse_qs(pdf_link_payload)
        pdf_link_payload_o = {k: v[0] for k, v in pdf_link_payload_o.items()}
        return pdf_link_payload_o

    def init_user_session(self, retries=0):
        try:
            res = requests.request(
            "GET", "https://judgments.ecourts.gov.in/pdfsearch/", verify=False, timeout=10
            )
            self.session_id = res.cookies.get(self.session_cookie_name)
            self.ecourts_token = res.cookies.get(self.ecourts_token_cookie_name)
        except Exception as e:
            print("Error when initializing session", e)
            if retries < 3:
                print("Retrying session initialization")
                self.init_user_session(retries + 1)


    def get_cookie(self):
        return f"{self.ecourts_token_cookie_name}={self.ecourts_token}; {self.session_cookie_name}={self.session_id}"

    def update_session_id(self, response):
        new_session_cookie = response.cookies.get(self.session_cookie_name)
        if new_session_cookie:
            self.session_id = new_session_cookie


def run():
    court_codes = COURT_CODES_ALL
    print("enter start date in format yyyy-mm-dd")

    start_date = input("Enter start date: \n")
    end_date = input("Enter end date: \n")
    court_code = "8~9"
    i = 0

    def process(court_code, start_date, end_date, thread_no):
        try:
            Downloader(court_code, start_date, end_date, thread_no).download()
        except Exception as e:
            traceback.print_exc()
            print("Error processing court", court_code, court_codes[court_code])

    with concurrent.futures.ThreadPoolExecutor(max_workers=25) as executor:
        futures = []
        all_dates = generate_date_tuples(start_date, end_date)

        for date_tuple in all_dates:
            futures.append(executor.submit(process, court_code, date_tuple[0], date_tuple[1], i))
            i += 1

        concurrent.futures.wait(futures)


if __name__ == "__main__":
    run()

"""
captcha prompt while downloading pdf seems to be different from session timeout
Every search API request returns a new app_token in response payload and new PHPSESSID in response cookies that need to be sent in the next request.
openpdfcaptcha request refreshes the app_token but not PHPSESSID

"""
