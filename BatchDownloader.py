import json
import traceback
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from download import root_url, reader
from utility import get_headers, extract_pdf_fragment, get_pdf_output_path, is_pdf_downloaded, default_pdf_link_payload, \
    default_search_payload
import lxml.html as LH


class BatchDownloader:
    def __init__(self, fragment_list, output_dir, court_code, court_name, ecourts_token_cookie_name, app_token, session_id, session_cookie_name, ecourts_token, thread_no):
        self.thread_no = thread_no
        self.app_token = app_token
        self.session_id = session_id
        self.session_cookie_name = session_cookie_name
        self.ecourts_token = ecourts_token
        self.ecourts_token_cookie_name = ecourts_token_cookie_name
        self.fragment_list = fragment_list
        self.total = len(fragment_list)
        self.existing = 0
        self.output_dir = output_dir
        self.court_code = court_code
        self.court_name = court_name
        self.pdf_link_url = f"{root_url}/pdfsearch/?p=pdf_search/openpdfcaptcha"
        self.captcha_token_url = f"{root_url}/pdfsearch/?p=pdf_search/checkCaptcha"
        self.captcha_url = f"{root_url}/pdfsearch/vendor/securimage/securimage_show.php"  # not lint skip/
        self.pdf_link_url_wo_captcha = f"{root_url}/pdfsearch/?p=pdf_search/openpdf"
        self.search_url = f"{root_url}/pdfsearch/?p=pdf_search/home/"
        self.pdf_already_exists = 0
        self.pdf_fresh_downloaded = 0

    def get_cookie(self):
        return f"{self.ecourts_token_cookie_name}={self.ecourts_token}; {self.session_cookie_name}={self.session_id}"

    def process_result_row(self, row, row_pos=-1):
        html = row[1]
        row_pos = row[0] - 1
        soup = BeautifulSoup(html, "html.parser")

        if not (soup.button and "onclick" in soup.button.attrs):
            print("No button found, likely multi language judgment")
            with open("html-parse-failures.txt", "a") as f:
                f.write(html + "\n")
            # TODO: requires special parsing
            return False, False
        pdf_fragment = extract_pdf_fragment(html_attribute=soup.button["onclick"])
        pdf_output_path = get_pdf_output_path(self.output_dir, pdf_fragment)
        pdf_exit_already = is_pdf_downloaded(self.output_dir, pdf_fragment)
        is_fresh_download_successful = not pdf_exit_already
        if not pdf_exit_already:
            is_fresh_download_successful = self.download_pdf(pdf_fragment, row_pos)
        metadata_output = pdf_output_path.with_suffix(".json")
        metadata = {
            "court_code": self.court_code,
            "court_name": self.court_name,
            "raw_html": html,
            "pdf_link": pdf_fragment,
            "downloaded": is_fresh_download_successful or pdf_exit_already,
        }
        metadata_output.parent.mkdir(parents=True, exist_ok=True)
        with open(metadata_output, "w") as f:
            json.dump(metadata, f)
        return pdf_exit_already, is_fresh_download_successful

    def update_session_id(self, response):
        new_session_cookie = response.cookies.get(self.session_cookie_name)
        if new_session_cookie:
            self.session_id = new_session_cookie

    def solve_captcha(self, retries=0, captcha_url=None):
        if captcha_url is None:
            captcha_url = self.captcha_url
        # download captch image and save
        captcha_response = requests.get(
            captcha_url, headers={"Cookie": self.get_cookie()}, verify=False, timeout=10
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

    def refresh_token(self, with_app_token=False):
        # print("Current session id ", self.session_id)
        # print("Current token ", self.app_token)
        captcha_text = self.solve_captcha()
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
            headers=get_headers(self.get_cookie(), root_url),
            data=captcha_check_payload,
            verify=False,
            timeout=10,
        )
        res_json = res.json()
        self.app_token = res_json["app_token"]
        self.update_session_id(res)
        print("Refreshed token")


    def request_api(self, method, url, payload, **kwargs):
        headers = get_headers(self.get_cookie(), root_url)
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

        elif response_dict.get("session_expire") == "Y":
            self.refresh_token()
            if payload:
                payload["app_token"] = self.app_token
            return self.request_api(method, url, payload, **kwargs)

        elif "errormsg" in response_dict:

            self.refresh_token()
            if payload:
                payload["app_token"] = self.app_token
            return self.request_api(method, url, payload, **kwargs)

        return response

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

    def download_pdf(self, pdf_fragment, row_pos):
        # prepare temp pdf request
        pdf_output_path = get_pdf_output_path(self.output_dir, pdf_fragment)
        pdf_link_payload = default_pdf_link_payload()
        pdf_link_payload["path"] = pdf_fragment
        pdf_link_payload["val"] = row_pos
        pdf_link_payload["app_token"] = self.app_token
        pdf_link_response = self.request_api(
            "POST", self.pdf_link_url, pdf_link_payload
        )
        if "outputfile" not in pdf_link_response.json():
            response = pdf_link_response.json()
            print("Error downloading pdf", response)
            if response.get("message") == "Invalid Captcha":
                raise Exception("Invalid Captcha. Raising this exception to retry from the top level which calls request_api")
            return False
        pdf_download_link = pdf_link_response.json()["outputfile"]

        # download pdf and save
        pdf_response = requests.request(
            "GET",
            root_url + pdf_download_link,
            verify=False,
            headers=get_headers(cookie=self.get_cookie(), root_url=root_url),
            timeout=10
        )
        pdf_output_path.parent.mkdir(parents=True, exist_ok=True)
        # number of response butes
        no_of_bytes = len(pdf_response.content)
        if no_of_bytes == 0:
            print("Empty pdf", pdf_output_path)
            raise Exception(
                "Invalid Captcha. Raising this exception to retry from the top level which calls request_api")
            # return False
        if no_of_bytes == 315:
            print("404 pdf response")
            return False
        with open(pdf_output_path, "wb") as f:
            f.write(pdf_response.content)
            f.flush()
        print(f"Downloaded {pdf_output_path}, size: {no_of_bytes}", self.thread_no)
        return True

    def init_user_session(self):
        res = requests.request(
            "GET", "https://judgments.ecourts.gov.in/pdfsearch/", verify=False, timeout=10
        )
        self.session_id = res.cookies.get(self.session_cookie_name)
        self.ecourts_token = res.cookies.get(self.ecourts_token_cookie_name)

    def download_all(self):
        # search_payload = default_search_payload()
        # search_payload["from_date"] = "2023-12-30"
        # search_payload["to_date"] = "2023-12-31"
        # self.init_user_session()
        # search_payload["state_code"] = self.court_code
        # search_payload["app_token"] = self.app_token
        # response = self.request_api("POST", self.search_url, search_payload)
        for row in self.fragment_list:
            try:
                pdf_exit_already, is_fresh_download_successful = self.process_result_row(
                    row, row_pos=-1
                )
                if pdf_exit_already:
                    self.pdf_already_exists += 1

                if is_fresh_download_successful:
                    self.pdf_fresh_downloaded += 1

            except Exception as e:
                if "Invalid Captcha" in str(e):
                    raise Exception("Invalid Captcha. Raising this exception to retry from the top level which calls request_api")
                print(e)
                traceback.print_stack(e)
                print("Error processing row", row)
