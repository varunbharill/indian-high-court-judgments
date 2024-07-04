import json
import re
import traceback
import urllib
from datetime import datetime, timedelta
from typing import Union
import threading

lock = threading.Lock()
payload = "&sEcho=1&iColumns=2&sColumns=,&iDisplayStart=0&iDisplayLength=100&mDataProp_0=0&sSearch_0=&bRegex_0=false&bSearchable_0=true&bSortable_0=true&mDataProp_1=1&sSearch_1=&bRegex_1=false&bSearchable_1=true&bSortable_1=true&sSearch=&bRegex=false&iSortCol_0=0&sSortDir_0=asc&iSortingCols=1&search_txt1=&search_txt2=&search_txt3=&search_txt4=&search_txt5=&pet_res=&state_code=27~1&state_code_li=&dist_code=null&case_no=&case_year=&from_date=&to_date=&judge_name=&reg_year=&fulltext_case_type=&int_fin_party_val=undefined&int_fin_case_val=undefined&int_fin_court_val=undefined&int_fin_decision_val=undefined&act=&sel_search_by=undefined&sections=undefined&judge_txt=&act_txt=&section_txt=&judge_val=&act_val=&year_val=&judge_arr=&flag=&disp_nature=&search_opt=PHRASE&date_val=ALL&fcourt_type=2&citation_yr=&citation_vol=&citation_supl=&citation_page=&case_no1=&case_year1=&pet_res1=&fulltext_case_type1=&citation_keyword=&sel_lang=&proximity=&neu_cit_year=&neu_no=&ajax_req=true&app_token=1fbc7fbb840eb95975c684565909fe6b3b82b8119472020ff10f40c0b1c901fe"
page_size = 1000

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


def get_tracking_data(with_lock=False):
    try:
        if with_lock:
            lock.acquire()
            tracking_data = get_json_file("./track.json")
            lock.release()
        else:
            tracking_data = get_json_file("./track.json")
        return tracking_data
    except Exception as e:
        traceback.print_exc()
        print(e, "couldn't load tracking data")
        return {}


def save_tracking_data(tracking_data):
    with open("./track.json", "w") as f:
        json.dump(tracking_data, f)


def save_court_tracking_date(court_code, court_tracking):
    # acquire a lock
    try:
        lock.acquire()
        tracking_data = get_tracking_data()
        all_date_dict = tracking_data.get(court_code, {})
        all_date_dict[court_tracking["from_date"] + "-" + court_tracking["to_date"]] = court_tracking
        tracking_data[court_code] = all_date_dict
        save_tracking_data(tracking_data)
        # release the lock
        lock.release()
    except Exception as e:
        traceback.print_exc()
        print("error saving tracking data", e)



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


def default_search_payload():
    search_payload = urllib.parse.parse_qs(payload)
    search_payload = {k: v[0] for k, v in search_payload.items()}
    search_payload["sEcho"] = 1
    search_payload["iDisplayStart"] = 0
    search_payload["iDisplayLength"] = page_size
    return search_payload


def generate_date_tuples(start_date, end_date):
    # Convert string dates to datetime objects
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    # Initialize an empty list to store the tuples
    date_tuples = []

    # Generate dates from start to end, inclusive
    current_date = start
    while current_date < end:
        next_date = current_date + timedelta(days=1)
        date_tuples.append((current_date.strftime("%Y-%m-%d"), next_date.strftime("%Y-%m-%d")))
        current_date = next_date

    return date_tuples
