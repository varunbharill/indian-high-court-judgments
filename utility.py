import json
import re
import urllib
from datetime import datetime, timedelta
from typing import Union
import threading

lock = threading.Lock()


def get_headers(cookie, root_url):
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9,pt;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Cookie": cookie,
        "DNT": "1",
        "Origin": root_url,
        "Referer": root_url + "/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest",
        "sec-ch-ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
    }
    return headers


def get_new_date_range(last_date: str) -> tuple[Union[str, None], Union[str, None]]:
    day_step = 1
    last_date_dt = datetime.strptime(last_date, "%Y-%m-%d")
    new_from_date_dt = last_date_dt + timedelta(days=1)
    new_to_date_dt = new_from_date_dt + timedelta(days=day_step - 1)
    if new_from_date_dt.date() > datetime.now().date():
        return None, None

    if new_to_date_dt.date() > datetime.now().date():
        new_to_date_dt = datetime.now().date()
    new_from_date = new_from_date_dt.strftime("%Y-%m-%d")
    new_to_date = new_to_date_dt.strftime("%Y-%m-%d")
    return new_from_date, new_to_date


def extract_pdf_fragment(html_attribute):
    pattern = r"javascript:open_pdf\('.*?','.*?','(.*?)'\)"
    match = re.search(pattern, html_attribute)
    if match:
        return match.group(1).split("#")[0]
    return None


def get_json_file(file_path) -> dict:
    with open(file_path) as f:
        return json.load(f)


def get_tracking_data():
    tracking_data = get_json_file("./track.json")
    return tracking_data


def save_tracking_data(tracking_data):
    with open("./track.json", "w") as f:
        json.dump(tracking_data, f)


def save_court_tracking_date(court_code, court_tracking):
    # acquire a lock
    lock.acquire()
    tracking_data = get_tracking_data()
    tracking_data[court_code] = court_tracking
    save_tracking_data(tracking_data)
    # release the lock
    lock.release()


def get_pdf_output_path(output_dir, pdf_fragment):
    return output_dir / pdf_fragment.split("#")[0]


def is_pdf_downloaded(output_dir, pdf_fragment):
    pdf_metadata_path = get_pdf_output_path(output_dir, pdf_fragment).with_suffix(".json")
    if pdf_metadata_path.exists():
        pdf_metadata = get_json_file(pdf_metadata_path)
        return pdf_metadata["downloaded"]
    return False

def default_pdf_link_payload():
    pdf_link_payload = "val=0&lang_flg=undefined&path=cnrorders/taphc/orders/2017/HBHC010262202017_1_2047-06-29.pdf#page=&search=+&citation_year=&fcourt_type=2&file_type=undefined&nc_display=undefined&ajax_req=true&app_token=c64944b84c687f501f9692e239e2a0ab007eabab497697f359a2f62e4fcd3d10"
    pdf_link_payload_o = urllib.parse.parse_qs(pdf_link_payload)
    pdf_link_payload_o = {k: v[0] for k, v in pdf_link_payload_o.items()}
    return pdf_link_payload_o
