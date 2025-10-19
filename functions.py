# functions.py
"""
Contains core scraping functions.
Is independent and importable by script.py or app.py.
"""

# region ------------------- Chapter 1: Imports -------------------

# For CLI
import requests
import time
import logging
import re
import html
import json
import os
import logging
import difflib
from pathlib import Path
from dateutil import parser as dateparser
from bs4 import BeautifulSoup
from datetime import datetime, timedelta, date
from typing import Optional, Dict, Any
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
    StaleElementReferenceException,
)

# For UI
import builtins
import streamlit as st
import mimetypes
import base64
import shutil
import urllib.parse
import streamlit.components.v1 as components


# For Streamlit Helper

from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    NoSuchElementException,
)

from PIL import Image
from io import BytesIO


# endregion Imports


# region ------------------- Chapter 2: Constants -------------------

# for CNR search
BASE_URL = "https://services.ecourts.gov.in/ecourtindia_v6/"

# for causelist download
SEARCH_PATH = "?p=cnr_status/searchByCNR/"
CAUSE_LIST_PAGE = BASE_URL + "?p=cause_list"
CAUSE_LIST_SUBMIT = BASE_URL + "?p=cause_list/submitCauseList"

# HTTP headers sent with each request
BASE_HEADERS = {
    "User-Agent": "ecourts-scraper/1.0 (+https://github.com/Mr-Artistic)",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://services.ecourts.gov.in",
    "Referer": BASE_URL,
    "X-Requested-With": "XMLHttpRequest",
}

# A logger system for printing errors
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# endregion Constants


# region ------------------- Chapter 3: Helper Functions -------------------


def save_json(data: Dict[str, Any], path: str):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info("Saved JSON to %s", p)


def download_file(url: str, dest: str, timeout: int = 30) -> Optional[str]:
    try:
        r = requests.get(url, stream=True, timeout=timeout)
        r.raise_for_status()
        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        logger.info("Downloaded file to %s", p)
        return str(p)
    except Exception as e:
        logger.error("Download failed: %s", e)
        return None


def is_date_today_or_tomorrow(date_obj: datetime) -> str:
    today = datetime.now().date()
    if date_obj.date() == today:
        return "today"
    if date_obj.date() == today + timedelta(days=1):
        return "tomorrow"
    return "other"


# endregion Helper Functions


# region ------------------- Chapter 4: CNR Functions -------------------


def make_soup(html_text: str):
    """Convert raw HTML into a BS object.
    Prefer lxml when available; fallback to built-in parser.
    """
    try:
        return BeautifulSoup(html_text, "lxml")
    except Exception:
        return BeautifulSoup(html_text, "html.parser")


def _download_captcha_with_retries(
    session, cap_src, out_path, base_page_url, tries=3, timeout=20
):
    """
    Download captcha image using session.
    Retry on transient failures.
    cap_src may be relative.
    out_path is a local file path to write.
    """
    cap_url = cap_src
    if not cap_src.startswith("http"):
        cap_url = urljoin(base_page_url, cap_src)

    headers = {
        "User-Agent": BASE_HEADERS.get("User-Agent", "python-requests"),
        "Referer": base_page_url,
    }

    last_exc = None
    for attempt in range(1, tries + 1):
        try:
            with session.get(
                cap_url, stream=True, timeout=timeout, headers=headers
            ) as cr:
                cr.raise_for_status()
                with open(out_path, "wb") as fh:
                    for chunk in cr.iter_content(8192):
                        fh.write(chunk)
            return out_path
        except Exception as e:
            last_exc = e
            time.sleep(0.8 * attempt)

    # If reached here, all retries failed
    raise last_exc


def _get_app_token_and_captcha(session: requests.Session) -> dict:
    """
    - Fetch eCourts home page
    - Extract app_token and captcha <img src>
    - Download captcha image (with retries) and save it to outputs/cnr
    """

    # 1. Fetch eCourts home page

    try:
        resp = session.get(
            BASE_URL, headers={"User-Agent": BASE_HEADERS["User-Agent"]}, timeout=20
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Failed to load home page: %s", e)
        return {"app_token": None, "captcha_path": ""}

    soup = make_soup(resp.text)

    # 2. Extract app_token (hidden input or JS var)

    token = None
    token_input = soup.find("input", {"name": "app_token"})
    if token_input and token_input.get("value"):
        token = token_input["value"].strip()
    else:
        m = re.search(r"app_token['\"]?\s*[:=]\s*['\"]([0-9a-fA-F]+)['\"]", resp.text)
        if m:
            token = m.group(1)

    # 3. Find captcha img element - search for securimage_show in src

    img = None
    for i in soup.find_all("img"):
        src = i.get("src", "")
        if "securimage_show" in src:
            img = i
            break

    if not img or not img.get("src"):
        logger.error("Captcha image not found on home page.")
        return {"app_token": token, "captcha_path": ""}

    captcha_src = img["src"]
    try:

        # 4. Download captcha --> outputs/cnr
        out_dir = os.path.join("outputs", "cnr")
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        captcha_file = os.path.join(out_dir, f"captcha_{timestamp}.jpg")
        # -> outputs/cnr/captcha_timestamp.jpg
        saved = _download_captcha_with_retries(
            session, captcha_src, captcha_file, BASE_URL, tries=4, timeout=20
        )
        logger.info("Captcha saved as %s ✅", saved)
        return {"app_token": token, "captcha_path": saved}
    except Exception as e:
        logger.error("Captcha download failed: %s", e)
        return {"app_token": token, "captcha_path": ""}


def _post_search(
    session: requests.Session, cino: str, fcaptcha_code: str, app_token: Optional[str]
) -> Optional[requests.Response]:
    """Perform the POST to search endpoint with required form fields."""
    url = BASE_URL + SEARCH_PATH
    payload = {
        "cino": cino,
        "fcaptcha_code": fcaptcha_code,
        "ajax_req": "true",
    }
    if app_token:
        payload["app_token"] = app_token

    headers = BASE_HEADERS.copy()
    # Content-Type is application/x-www-form-urlencoded by default when using data=
    try:
        resp = session.post(url, data=payload, headers=headers, timeout=20)
        resp.raise_for_status()
        return resp
    except requests.RequestException as e:
        logger.error("POST search failed: %s", e)
        return None


def _extract_label_value(soup: BeautifulSoup, label: str) -> Optional[str]:
    """
    Find text node matching label (case-insensitive), then return the sibling cell text.
    Works for HTML tables where label and value live in adjacent TD/TH.
    """

    # Look for exact text in page
    node = soup.find(text=re.compile(re.escape(label), re.I))
    if not node:
        return None

    # Try to find enclosing row and the next cell
    parent_tr = node.find_parent("tr")
    if parent_tr:
        tds = parent_tr.find_all(["td", "th"])

        # Find index of the label cell, return next cell if exists
        for i, cell in enumerate(tds):
            if cell and re.search(
                re.escape(label), cell.get_text(" ", strip=True), re.I
            ):
                if i + 1 < len(tds):
                    return tds[i + 1].get_text(" ", strip=True)

    # Fallback: look for next sibling element text
    try:
        nxt = node.parent.find_next_sibling()
        if nxt:
            return nxt.get_text(" ", strip=True)
    except Exception:
        pass
    return None


def parse_case_html(html: str) -> Dict[str, Any]:
    """Given the returned HTML, extract useful fields."""

    soup = make_soup(html)
    result = {
        "listed": True,
        "cnr": _extract_label_value(soup, "CNR Number")
        or _extract_label_value(soup, "CNR")
        or None,
        "case_type": _extract_label_value(soup, "Case Type"),
        "filing_date": _extract_label_value(soup, "Filing Date"),
        "registration_number": _extract_label_value(soup, "Registration Number"),
        "first_hearing_date": _extract_label_value(soup, "First Hearing Date"),
        "next_hearing_date": _extract_label_value(soup, "Next Hearing Date"),
        "case_stage": _extract_label_value(soup, "Case Stage"),
        "court_name": _extract_label_value(soup, "Court Number and Judge")
        or _extract_label_value(soup, "Court"),
        "raw_html_snippet": html[:5000],
    }

    # Try to find PDF link (anchor ending with .pdf or 'Order on Exhibit' links)
    pdf = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
    if pdf:
        result["pdf_url"] = pdf.get("href")
    else:
        result["pdf_url"] = None

    return result


def get_case_listing_by_cnr_interactive(cnr: str) -> Dict[str, Any]:
    """
    High-level function:
    - start a session
    - fetch app_token + captcha image
    - prompt user to enter captcha
    - post data and parses the returned HTML
    - example cli use: python script.py --cnr MHPU050000272025
    """

    session = requests.Session()
    # 1. Init + get app_token + captcha
    info = _get_app_token_and_captcha(session)
    app_token = info.get("app_token")
    captcha_path = info.get("captcha_path")

    if captcha_path:
        logger.info("Captcha saved to %s — open and enter the value.", captcha_path)
    else:
        logger.warning(
            "Captcha image not fetched. You may still try, but server may reject the request."
        )

    # 2. Get captcha from user
    fcaptcha_code = input(
        "Enter captcha shown in captcha.jpg (or press Enter if none): "
    ).strip()
    if not fcaptcha_code:
        logger.warning("No captcha entered; server may reject the request.")

    # small polite delay
    time.sleep(0.5)

    # 3. POST Search
    resp = _post_search(
        session, cino=cnr, fcaptcha_code=fcaptcha_code, app_token=app_token
    )
    if resp is None:
        return {"error": "network", "cnr": cnr}

    # 4. Parse response HTML
    parsed = parse_eCourts_response(resp)
    parsed["session_cookies"] = session.cookies.get_dict()

    # 5. Save result to JSON file
    os.makedirs("outputs/cnr", exist_ok=True)
    with open(f"outputs/cnr/{cnr}.json", "w", encoding="utf-8") as f:
        json.dump(parsed, f, ensure_ascii=False, indent=2)

    return parsed


def _clean_html_from_json_field(txt: str) -> str:
    """
    Convert JSON-escaped HTML string into normal HTML for BeautifulSoup.
    - replace escaped slashes and unescapes HTML entities.
    - strip leading/trailing quotes if present.
    """

    if not txt:
        return ""
    # If it's already a dict/JSON, stringify
    if isinstance(txt, (dict, list)):
        txt = json.dumps(txt)
    # Replace common escapes
    s = txt.replace("\\/", "/")
    s = s.replace("\\n", "\n").replace("\\t", "    ")
    # Unescape HTML entities (&amp; -> & etc.)
    s = html.unescape(s)
    # Sometimes the JSON contains quoted string; remove outer quotes if present
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s


def _soup_from_resp(resp) -> BeautifulSoup:
    """
    Given a requests.Response, return a BeautifulSoup object.
    Handle:
      - JSON response containing an HTML field
      - Direct HTML response
    """

    content_type = resp.headers.get("Content-Type", "")
    text = ""
    # if JSON-like, try json()
    if "application/json" in content_type or resp.text.strip().startswith("{"):
        try:
            j = resp.json()
        except Exception:
            # fallback: parse text as JSON-ish
            try:
                j = json.loads(resp.text)
            except Exception:
                j = {}
        # find likely HTML field(s)
        html_source = None
        # preferred keys to inspect (based on observed response)
        for key in ("raw_html_snippet", "html", "data", "result"):
            if key in j and j[key]:
                html_source = j[key]
                break
        # otherwise take the first string value that looks like HTML
        if not html_source:
            for k, v in j.items():
                if isinstance(v, str) and ("<table" in v or "<div" in v or "<h3" in v):
                    html_source = v
                    break
        if html_source:
            text = _clean_html_from_json_field(html_source)
        else:
            # fallback - just take whole response text cleaned
            text = _clean_html_from_json_field(resp.text)
    else:
        text = resp.text

    return BeautifulSoup(text, "lxml")


def find_label_value(soup: BeautifulSoup, label: str) -> Optional[str]:
    """
    Find table label cell containing 'label' (case-insensitive),
    then return the adjacent value cell text.
    """

    node = soup.find(text=re.compile(re.escape(label), re.I))
    if not node:
        return None
    # climb to the row
    tr = node.find_parent("tr")
    if tr:
        cells = tr.find_all(["td", "th"])
        for i, cell in enumerate(cells):
            if re.search(re.escape(label), cell.get_text(" ", strip=True), re.I):
                # return next cell(s) combined if present
                if i + 1 < len(cells):
                    return cells[i + 1].get_text(" ", strip=True)
    # fallback: check sibling nodes
    try:
        nxt = node.parent.find_next_sibling()
        if nxt:
            return nxt.get_text(" ", strip=True)
    except Exception:
        pass
    return None


def parse_eCourts_response(resp) -> Dict[str, Any]:
    """
    Given a requests.Response from the search POST, return a clean dict with fields:
    cnr, case_type, filing_date, first_hearing_date, next_hearing_date,
    case_stage, court_name, pdf_url, case_history (list), interim_orders (list), listed(bool)
    """
    soup = _soup_from_resp(resp)
    out: Dict[str, Any] = {"listed": False}

    # Basic fields
    out["cnr"] = find_label_value(soup, "CNR Number") or find_label_value(soup, "CNR")
    out["case_type"] = find_label_value(soup, "Case Type")
    out["filing_date"] = find_label_value(soup, "Filing Date")
    out["registration_number"] = find_label_value(soup, "Registration Number")
    out["first_hearing_date"] = find_label_value(soup, "First Hearing Date")
    out["next_hearing_date"] = find_label_value(soup, "Next Hearing Date")
    out["case_stage"] = find_label_value(soup, "Case Stage")
    out["court_name"] = find_label_value(
        soup, "Court Number and Judge"
    ) or find_label_value(soup, "Court")
    out["serial_number"] = None
    out["court_name_clean"] = None
    out["judge_name_and_court_address"] = None

    if out.get("court_name"):
        # Example string: "1-CIVIL JUDGE J.D. AND J.M.F.C. PMC PUNE"
        text = out["court_name"].strip()

        # Extract serial number (digits before hyphen)
        m = re.match(r"\s*(\d+)\s*-\s*(.*)", text)
        if m:
            out["serial_number"] = m.group(1)
            remainder = m.group(2)
        else:
            remainder = text

        # Split remaining part into court name and judge info
        # Capture words before "JUDGE" (case-insensitive)
        n = re.search(r"([A-Z\s]+?)\s*JUDGE", remainder, re.I)
        if n:
            court_name = n.group(1).strip()
            out["court_name_clean"] = court_name.title()  # e.g., "Civil"
            # Everything from "JUDGE" onward
            judge_info = remainder[n.end() - len("JUDGE") :].strip()
            out["judge_name_and_court_address"] = judge_info
        else:
            # fallback if "JUDGE" keyword not found
            out["court_name_clean"] = remainder
            out["judge_name_and_court_address"] = None

    # PDF link (order/exhibit) detection
    pdf_a = soup.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
    out["pdf_url"] = pdf_a.get("href") if pdf_a else None
    if out["pdf_url"]:
        pdf_name = out["pdf_url"].split("/")[-1]
        pdf_path = Path("outputs/cnr") / pdf_name
        try:
            r = requests.get(out["pdf_url"], timeout=30)
            r.raise_for_status()
            pdf_path.write_bytes(r.content)
            out["pdf_path"] = str(pdf_path)
            logger.info(f"Downloaded PDF: {pdf_path}")
        except Exception as e:
            logger.warning(f"Failed to download PDF: {e}")
            out["pdf_path"] = None

    # Case history (table rows)
    out["case_history"] = []
    history_table = soup.find(
        "table", {"class": re.compile(r"history_table|history", re.I)}
    )
    if history_table:
        for tr in history_table.find_all("tr"):
            cols = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
            if cols and len(cols) >= 3:
                out["case_history"].append(
                    {
                        "judge": cols[0],
                        "business_on_date": cols[1],
                        "hearing_date": cols[2],
                        "purpose": cols[3] if len(cols) > 3 else "",
                    }
                )

    # Interim orders (if any)
    out["interim_orders"] = []
    orders_table = soup.find("h3", string=re.compile(r"Interim Orders", re.I))
    if orders_table:
        orders_table = orders_table.find_next("table")
        if orders_table:
            for tr in orders_table.find_all("tr"):
                cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if cols:
                    out["interim_orders"].append(cols)

    # Determine 'listed' and check if today/tomorrow
    # Try to parse next_hearing_date or first_hearing_date
    def parse_date_try(s: Optional[str]):
        if not s:
            return None
        # remove stray words/elements like 'th' or ordinal words
        s_clean = re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", s)
        try:
            return dateparser.parse(s_clean, dayfirst=True)
        except Exception:
            return None

    next_dt = parse_date_try(out.get("next_hearing_date"))
    first_dt = parse_date_try(out.get("first_hearing_date"))
    chosen_date = next_dt or first_dt
    out["listed"] = bool(chosen_date)

    if chosen_date:
        today = datetime.now().date()
        if chosen_date.date() == today:
            out["listed_when"] = "today"
        elif chosen_date.date() == today + timedelta(days=1):
            out["listed_when"] = "tomorrow"
        else:
            out["listed_when"] = "other"
        out["next_hearing_date_parsed"] = chosen_date.isoformat()
    else:
        out["listed_when"] = "none"

    # tidy strings: remove leftover backslashes and extra whitespace
    for k, v in list(out.items()):
        if isinstance(v, str):
            out[k] = (
                v.replace("\\/", "/").replace("\\n", " ").replace("\\t", " ").strip()
            )

    return out


# endregion CNR Functions


# region ------------------- Chapter 5: Public API for CNR Search -------------------


def get_case_listing(
    cnr: Optional[str] = None,
    case_type: Optional[str] = None,
    case_number: Optional[int] = None,
    year: Optional[int] = None,
) -> Dict[str, Any]:
    if cnr:
        return get_case_listing_by_cnr_interactive(cnr)
    return {
        "error": "not_implemented",
        "message": "Case-type search not implemented; supply CNR for now.",
    }


# endregion Public API for CNR Search


# region ------------------- Chapter 6: Decoration for CNR Search results -------------------


def print_case_summary(data: Dict[str, Any]):
    """Print a compact human-friendly case summary to console."""

    print("\n📘 Case Summary")
    print("────────────────────────────────────────────────")
    print(f"🆔 CNR Number       : {data.get('cnr')}")
    print(
        f"🏛️ Court Name       : {data.get('court_name_clean') or data.get('court_name')}"
    )
    print(f"🔢 Serial Number    : {data.get('serial_number')}")
    print(f"👨‍⚖️ Judge & Address : {data.get('judge_name_and_court_address')}")
    print(f"📅 Next Hearing     : {data.get('next_hearing_date')}")
    print(f"📌 Case Stage       : {data.get('case_stage')}")
    print(f"🕒 Listed When      : {data.get('listed_when')}")
    print("────────────────────────────────────────────────")
    if data.get("pdf_path"):
        print(f"📄 PDF downloaded at: {data['pdf_path']}")
    else:
        print("⚠️ No PDF available for this case.")


def print_case_history(data: Dict[str, Any]):
    """Print a readable case history table to console (if available)."""
    history = data.get("case_history", [])
    if not history:
        print("\n📜 No Case History found.")
        return

    print("\n📜 Case History")
    print("────────────────────────────────────────────────")
    print(f"{'Judge':40} {'Business Date':15} {'Hearing Date':15} {'Purpose':20}")
    print("────────────────────────────────────────────────")
    for item in history:
        judge = (item.get("judge") or "-")[:38]
        business = (item.get("business_on_date") or "-")[:15]
        hearing = (item.get("hearing_date") or "-")[:15]
        purpose = (item.get("purpose") or "-")[:20]
        print(f"{judge:40} {business:15} {hearing:15} {purpose:20}")
    print("────────────────────────────────────────────────\n")


def generate_text_report(data: Dict[str, Any], file_path):
    """
    Generate a human-readable .txt report.
    """
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("eCourts Case Report\n")
    lines.append("==============================================\n")
    lines.append(f"CNR Number       : {data.get('cnr')}\n")
    lines.append(
        f"Court Name       : {data.get('court_name_clean') or data.get('court_name')}\n"
    )
    lines.append(f"Serial Number    : {data.get('serial_number')}\n")
    lines.append(f"Judge & Address  : {data.get('judge_name_and_court_address')}\n")
    lines.append(f"Next Hearing     : {data.get('next_hearing_date')}\n")
    lines.append(f"Case Stage       : {data.get('case_stage')}\n")
    lines.append(f"Listed When      : {data.get('listed_when')}\n")
    lines.append("==============================================\n\n")

    history = data.get("case_history", [])
    if history:
        lines.append("CASE HISTORY\n")
        lines.append("----------------------------------------------\n")
        lines.append(
            f"{'Judge':40} {'Business Date':15} {'Hearing Date':15} {'Purpose':20}\n"
        )
        lines.append("----------------------------------------------\n")
        for item in history:
            lines.append(
                f"{(item.get('judge') or '-')[:38]:40} "
                f"{(item.get('business_on_date') or '-')[:15]:15} "
                f"{(item.get('hearing_date') or '-')[:15]:15} "
                f"{(item.get('purpose') or '-')[:20]:20}\n"
            )
        lines.append("----------------------------------------------\n\n")
    else:
        lines.append("No case history available.\n\n")

    if data.get("pdf_path"):
        lines.append(f"PDF downloaded at: {data['pdf_path']}\n")

    p.write_text("".join(lines), encoding="utf-8")
    logger.info("📝 Text report generated: %s", p)


# endregion Decoration for CNR Search results


# region ------------------- Chapter 7: Causelist Functions -------------------


def _parse_select_options(soup, select_name_candidates):
    """
    Try multiple candidate names/ids and return list of dicts {value, text}.
    select_name_candidates: list of names or ids to try, e.g. ["state_code", "state"]
    """

    for name in select_name_candidates:
        sel = soup.find("select", {"name": name}) or soup.find("select", {"id": name})
        if sel:
            opts = []
            for o in sel.find_all("option"):
                v = (o.get("value") or "").strip()
                t = (o.text or "").strip()
                if v and v.lower() not in ("0", "select", "null", ""):
                    opts.append({"value": v, "text": t})
            if opts:
                return opts
    # fallback: fuzzy find any select that contains keywords in its name/id
    return []


def _resolve_name_or_code(opts, user_input):
    """
    opts: list of {'value','text'}
    user_input: either a numeric code or a name/substr
    Returns selected option dict or None.
    Behavior:
        - if user_input looks numeric and matches a value -> return value-match
        - exact text match (case-ins)
        - substring match (case-ins)
        - difflib fuzzy match (best single result)
    """
    if not user_input:
        return None
    s = str(user_input).strip()
    # numeric code match (exact)
    for o in opts:
        if o["value"] == s:
            return o
    # exact text match (case-ins)
    for o in opts:
        if o["text"].lower() == s.lower():
            return o
    # substring match
    subs = [o for o in opts if s.lower() in o["text"].lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        # multiple candidates: ask user to choose
        print(f"Multiple matches found for '{s}':")
        for i, cand in enumerate(subs, 1):
            print(f"  [{i}] {cand['text']} (value={cand['value']})")
        sel = input("Pick number (or press Enter to pick first): ").strip()
        try:
            idx = int(sel) - 1
            if 0 <= idx < len(subs):
                return subs[idx]
        except Exception:
            return subs[0]
    # fuzzy match on visible text
    texts = [o["text"] for o in opts]
    best = difflib.get_close_matches(s, texts, n=1, cutoff=0.6)
    if best:
        for o in opts:
            if o["text"] == best[0]:
                return o
    return None


def download_entire_cause_list(
    session: requests.Session,
    state: str,
    district: str,
    court_complex: Optional[str],
    court_name: Optional[str],
    out_dir: str = "outputs/causelists",
    interactive: bool = True,
    max_retries_on_popup: int = 3,
    causelist_date: Optional[str] = None,
):
    """
    Uses requests to download causelist for a single (provided: state, district, court_complex, court_name).
        - state, district, court_complex, court_name: visible substrings
        - session: an existing requests.Session() (keeps cookies)
        - interactive: if True, will ask for captcha (uses _get_app_token_and_captcha())
        - tries to detect popup/error banners in HTML responses and retries a few times
    """

    os.makedirs(out_dir, exist_ok=True)

    def try_close_in_html(html_text: str) -> bool:
        """
        Heuristic for 'closing' popups when using requests:
        - Look for known error/banner text ("Invalid Request", "Oops", "Try once again")
        - Returns True if we think page contained a blocking banner (so caller may retry/post again)
        """
        if not html_text:
            return False
        low = html_text.lower()
        # common blocking strings observed in Selenium flow
        blockers = [
            "invalid request",
            "oops",
            "try once again",
            "please try again",
            "access denied",
        ]
        for b in blockers:
            if b in low:
                return True
        return False

    # 1) Load landing page and parse selects
    resp = session.get(
        CAUSE_LIST_PAGE, headers={"User-Agent": BASE_HEADERS["User-Agent"]}, timeout=30
    )
    resp.raise_for_status()
    soup = make_soup(resp.text)

    # parse candidate selects/options
    states = _parse_select_options(
        soup, ["sess_state_code", "state_code", "state", "ddl_state_code", "state_name"]
    )
    dists = _parse_select_options(
        soup,
        ["sess_dist_code", "dist_code", "district_code", "district", "ddl_dist_code"],
    )
    complexes = _parse_select_options(soup, ["court_complex_code", "court_complex"])
    courts = _parse_select_options(
        soup, ["CL_court_no", "court_name", "court_name_txt"]
    )

    # Resolve inputs to codes
    state_opt = _resolve_name_or_code(states, state)
    if not state_opt:
        raise SystemExit(
            f"Could not resolve state '{state}'. Try one of: {[o['text'] for o in states[:20]]}"
        )
    dist_opt = _resolve_name_or_code(dists, district)
    if not dist_opt:
        raise SystemExit(
            f"Could not resolve district '{district}'. Try one of: {[o['text'] for o in dists[:30]]}"
        )

    state_code = state_opt["value"]
    dist_code = dist_opt["value"]

    # 2) If complexes/courts missing or not complete, POST state+dist to get server-populated selects
    try:
        # find the real state field name to post (fallback to 'state_code')
        state_field_name = "state_code"
        for cand in [
            "sess_state_code",
            "state_code",
            "state",
            "ddl_state_code",
            "state_name",
        ]:
            el = soup.find("select", {"name": cand}) or soup.find(
                "select", {"id": cand}
            )
            if el:
                state_field_name = el.get("name") or el.get("id") or state_field_name
                break

        r2 = session.post(
            CAUSE_LIST_PAGE,
            data={state_field_name: state_code},
            headers={"User-Agent": BASE_HEADERS["User-Agent"]},
            timeout=20,
        )
        if r2.ok:
            soup2 = make_soup(r2.text)
            dists = (
                _parse_select_options(
                    soup2,
                    [
                        "sess_dist_code",
                        "dist_code",
                        "district_code",
                        "district",
                        "ddl_dist_code",
                    ],
                )
                or dists
            )
            complexes = (
                _parse_select_options(soup2, ["court_complex_code", "court_complex"])
                or complexes
            )
            courts = (
                _parse_select_options(
                    soup2, ["CL_court_no", "court_name", "court_name_txt"]
                )
                or courts
            )
            soup = soup2
    except Exception:
        # harmless fallback
        pass

    # 3) Resolve complex and court
    complex_opt = _resolve_name_or_code(complexes, court_complex)
    if not complex_opt:
        raise SystemExit(
            f"Court complex '{court_complex}' not found. Sample: {[c['text'] for c in complexes[:30]]}"
        )

    # If courts are dynamically tied to complex, POST complex to obtain the courts list
    try:
        r3 = session.post(
            CAUSE_LIST_PAGE,
            data={
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": complex_opt["value"],
            },
            headers={"User-Agent": BASE_HEADERS["User-Agent"]},
            timeout=20,
        )
        if r3.ok:
            s3 = make_soup(r3.text)
            courts = (
                _parse_select_options(
                    s3, ["CL_court_no", "court_name", "court_name_txt"]
                )
                or courts
            )
    except Exception:
        pass

    court_opt = _resolve_name_or_code(courts, court_name)
    if not court_opt:
        raise SystemExit(
            f"Court '{court_name}' not found. Sample: {[c['text'] for c in courts[:40]]}"
        )

    # 4) Build the submit payload similar to Selenium -> then POST submitCauseList
    # Resolve user-supplied causelist_date or fallback to today
    if causelist_date:
        try:
            chosen_dt = dateparser.parse(causelist_date, dayfirst=True)
        except Exception:
            raise ValueError(f"Could not parse causelist_date: {causelist_date}")
    else:
        chosen_dt = datetime.now()
    today = chosen_dt.strftime("%d-%m-%Y")  # for form input on website (DD-MM-YYYY)
    iso_for_files = chosen_dt.date().isoformat()  # for filenames (YYYY-MM-DD)

    results = []
    # iterate cicri as in Selenium (Civil/Criminal)
    for cicri in ("civ", "cri"):
        payload = {
            "state_code": state_code,
            "dist_code": dist_code,
            "court_complex_code": complex_opt["value"],
            "CL_court_no": court_opt["value"],
            "court_name_txt": court_opt["text"],
            "causelist_date": today,
            "cicri": cicri,
            "est_code": "",
        }

        # interactive captcha: use helper to download captcha into outputs/cnr and ask user
        if interactive:
            info = _get_app_token_and_captcha(session)
            cap_path = info.get("captcha_path")
            if cap_path:
                print(
                    f"Captcha saved to {cap_path}. Open and enter the text for {court_opt['text']} ({cicri})."
                )
                payload["cause_list_captcha_code"] = input("Enter captcha: ").strip()
            else:
                payload["cause_list_captcha_code"] = input(
                    "Enter captcha (captcha image not found): "
                ).strip()
        else:
            payload["cause_list_captcha_code"] = ""

        # make the POST, but be prepared to retry if response HTML looks like a popup/banner
        attempt = 0
        saved = None
        while attempt < max_retries_on_popup:
            attempt += 1
            try:
                resp = session.post(
                    CAUSE_LIST_SUBMIT,
                    data=payload,
                    headers={
                        "User-Agent": BASE_HEADERS["User-Agent"],
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    timeout=30,
                )
                resp.raise_for_status()
            except Exception as e:
                logger.warning(
                    "POST failed for %s/%s (%s): %s",
                    court_opt["text"],
                    cicri,
                    attempt,
                    e,
                )
                # short sleep and retry
                time.sleep(0.8 * attempt)
                continue

            # quick heuristic: if returned JSON contains pdf url, handle it
            parsed_ok = False
            try:
                j = resp.json()
                pdf_url = (
                    j.get("pdfUrl")
                    or j.get("pdf_url")
                    or j.get("cause_list_pdf")
                    or j.get("pdf")
                )
                if pdf_url:
                    if not pdf_url.startswith("http"):
                        pdf_url = urljoin(BASE_URL, pdf_url)
                    outfn = os.path.join(
                        out_dir,
                        f"causelist_{court_opt['value']}_{cicri}_{iso_for_files}.pdf",
                    )
                    download_file(pdf_url, outfn)
                    saved = outfn
                    parsed_ok = True
            except Exception:
                # not json or parse failed — continue to html parsing
                pass

            if parsed_ok:
                break  # got pdf, done for this cicri

            # If response looks like HTML, inspect for pdf anchor or banners
            sresp = make_soup(resp.text)
            # If HTML contains a blocking banner, we will retry (re-post) up to max_retries_on_popup
            if try_close_in_html(resp.text):
                logger.info(
                    "Detected blocking banner in server response (attempt %d). Retrying...",
                    attempt,
                )
                time.sleep(0.6 * attempt)
                continue

            # try to find anchor to pdf
            a_pdf = sresp.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
            if a_pdf:
                pdf_url = a_pdf["href"]
                if not pdf_url.startswith("http"):
                    pdf_url = urljoin(BASE_URL, pdf_url)
                outfn = os.path.join(
                    out_dir,
                    f"causelist_{court_opt['value']}_{cicri}_{iso_for_files}.pdf",
                )
                try:
                    download_file(pdf_url, outfn)
                    saved = outfn
                except Exception as e:
                    logger.warning("Failed to download PDF at %s : %s", pdf_url, e)
                    saved = None
                break

            # fallback: attempt to extract tables or page HTML (like Selenium)
            try:
                tables = sresp.find_all("table")
                all_tables_html = ""
                for tbl in tables:
                    all_tables_html += str(tbl) + "\n"
                if not all_tables_html.strip():
                    # save entire body as fallback
                    all_tables_html = (
                        sresp.body.decode_contents() if sresp.body else resp.text
                    )
                fn = os.path.join(
                    out_dir,
                    f"causelist_{court_opt['value']}_{cicri}_{iso_for_files}.html",
                )
                with open(fn, "w", encoding="utf-8") as fh:
                    fh.write(all_tables_html)
                saved = fn
                # Attempt to parse into JSON (best-effort)
                try:
                    json_out = fn.replace(".html", ".json")
                    parse_cause_list_html(fn, json_out)
                except Exception as e:
                    logger.warning("Auto-parse failed for %s: %s", fn, e)
                break
            except Exception as e:
                logger.warning(
                    "Failed to extract HTML tables for %s/%s: %s",
                    court_opt["text"],
                    cicri,
                    e,
                )
                # retry if server likely sent a popup/temporary error
                if attempt < max_retries_on_popup:
                    time.sleep(0.6 * attempt)
                    continue
                break

        # record result for this cicri
        results.append({"court": court_opt["text"], "cicri": cicri, "saved": saved})
        # small pause between cicri attempts
        time.sleep(0.3)

    # save index for this court
    idx_path = os.path.join(
        out_dir, f"causelist_index_{court_opt['value']}_{iso_for_files}.json"
    )
    save_json(results, idx_path)
    print("Index saved to", idx_path)
    return idx_path


def download_entire_cause_list_selenium(
    state: str,
    district: str,
    court_complex: Optional[str],
    court_name: Optional[str],
    out_dir: str = "outputs/causelists",
    headless: bool = False,
    wait_timeout: int = 20,
    causelist_date: Optional[str] = None,
):
    """
    High Level Function:

    Uses Selenium as the java script is only triggered by user input clicks.
    Download causelist for a single (provided: state, district, court_complex, court_name).
    - state/district/court_complex/court_name: visible text (substring match, case-insensitive)
    - out_dir: where to save PDFs/HTML/captcha
    - headless: run Chrome headless if True (not recommended for interactive captcha solving)
    - wait_timeout: seconds for explicit waits
    """
    os.makedirs(out_dir, exist_ok=True)

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,1200")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    wait = WebDriverWait(driver, wait_timeout)

    def try_close_alerts(drv):
        """1. Auto close pop-up.\
        Try several strategies to close modals/alerts that block interaction."""
        try:
            # common close buttons
            for sel in (
                "button[aria-label='Close']",
                "button.close",
                "button.btn-close",
                ".swal2-close",
            ):
                try:
                    els = drv.find_elements(By.CSS_SELECTOR, sel)
                    if els:
                        for e in els:
                            try:
                                e.click()
                                time.sleep(0.25)
                                return True
                            except Exception:
                                pass
                except Exception:
                    pass
            # sweetalert/popup 'x' by xpath
            try:
                btn = drv.find_element(
                    By.XPATH, "//button[normalize-space()='×' or normalize-space()='X']"
                )
                btn.click()
                time.sleep(0.25)
                return True
            except Exception:
                pass
            # dismiss any visible banner containing "Invalid Request" / "Oops"
            banners = drv.find_elements(
                By.XPATH,
                "//*[contains(text(), 'Invalid Request') or contains(text(), 'Oops') or contains(text(),'Try once again')]",
            )
            for b in banners:
                try:
                    # try to click a close inside parent
                    parent = b.find_element(By.XPATH, "./ancestor::div[1]")
                    for close_candidate in parent.find_elements(
                        By.CSS_SELECTOR, "button, a"
                    ):
                        try:
                            close_candidate.click()
                            time.sleep(0.25)
                            return True
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return False

    def ensure_element_refreshed(getter_fn, retries=3, delay=0.5):
        """Call getter_fn() and retry a few times (useful for stale elements)."""
        for _ in range(retries):
            try:
                el = getter_fn()
                return el
            except Exception:
                time.sleep(delay)
        raise

    try:
        driver.get(CAUSE_LIST_PAGE)
        time.sleep(0.6)
        try_close_alerts(driver)

        # 2. Select State
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "select")))

        def find_state_select():
            for s in driver.find_elements(By.TAG_NAME, "select"):
                name = (s.get_attribute("name") or s.get_attribute("id") or "").lower()
                try:
                    opt_count = len(s.find_elements(By.TAG_NAME, "option"))
                except Exception:
                    opt_count = 0
                if "state" in name or opt_count > 10:
                    return s
            return None

        state_sel_el = find_state_select()
        if not state_sel_el:
            driver.quit()
            raise SystemExit("State select not found on page.")

        sel_state = Select(state_sel_el)
        # choose state by substring
        chosen_state_opt = None
        for opt in sel_state.options:
            if state.strip().lower() in (opt.text or "").strip().lower():
                chosen_state_opt = opt
                break
        if not chosen_state_opt:
            available = [o.text for o in sel_state.options[:30]]
            driver.quit()
            raise SystemExit(
                f"State '{state}' not found. Sample available: {available}"
            )

        sel_state.select_by_value(chosen_state_opt.get_attribute("value"))
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            state_sel_el,
        )
        logger.info("Selected state: %s", chosen_state_opt.text)
        time.sleep(0.4)
        try_close_alerts(driver)

        # 3. Select District
        def district_populated(drv):
            for s in drv.find_elements(By.TAG_NAME, "select"):
                n = (s.get_attribute("name") or "").lower()
                i = (s.get_attribute("id") or "").lower()
                if "dist" in n or "district" in n or "dist" in i:
                    opts = s.find_elements(By.TAG_NAME, "option")
                    real = [
                        o
                        for o in opts
                        if (o.get_attribute("value") or "").strip()
                        not in ("", "0", None)
                    ]
                    return len(real) >= 1
            return False

        WebDriverWait(driver, wait_timeout).until(district_populated)

        district_el = None
        for s in driver.find_elements(By.TAG_NAME, "select"):
            n = (s.get_attribute("name") or "").lower()
            i = (s.get_attribute("id") or "").lower()
            if "dist" in n or "district" in n or "dist" in i:
                district_el = s
                break
        if not district_el:
            driver.quit()
            raise SystemExit("District select not found after selecting state.")

        sel_district = Select(district_el)
        chosen_dist = None
        for opt in sel_district.options:
            if district.strip().lower() in (opt.text or "").strip().lower():
                chosen_dist = opt
                break
        if not chosen_dist:
            available = [o.text for o in sel_district.options[:50]]
            driver.quit()
            raise SystemExit(
                f"District '{district}' not found. Sample available: {available}"
            )

        sel_district.select_by_value(chosen_dist.get_attribute("value"))
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            district_el,
        )
        logger.info("Selected district: %s", chosen_dist.text)
        time.sleep(0.5)
        try_close_alerts(driver)

        # 4. Select Court Complex
        complex_el = None
        for s in driver.find_elements(By.TAG_NAME, "select"):
            name = (s.get_attribute("name") or "").lower()
            id_ = (s.get_attribute("id") or "").lower()
            if "complex" in name or "court_complex" in id_ or "court_complex" in name:
                complex_el = s
                break
        if not complex_el:
            for s in driver.find_elements(By.TAG_NAME, "select"):
                if s == state_sel_el or s == district_el:
                    continue
                if len(s.find_elements(By.TAG_NAME, "option")) > 1:
                    complex_el = s
                    break

        if not complex_el:
            driver.quit()
            raise SystemExit("Court complex select not found.")

        sel_complex = Select(complex_el)
        chosen_complex = None
        for opt in sel_complex.options:
            if court_complex.strip().lower() in (opt.text or "").strip().lower():
                chosen_complex = opt
                break
        if not chosen_complex:
            available = [o.text for o in sel_complex.options[:40]]
            driver.quit()
            raise SystemExit(
                f"Court complex '{court_complex}' not found. Sample available: {available}"
            )

        # select complex & fire change
        sel_complex.select_by_value(chosen_complex.get_attribute("value"))
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            complex_el,
        )
        logger.info("Selected court complex: %s", chosen_complex.text)
        time.sleep(0.6)
        try_close_alerts(driver)

        # 5. Select Court Name
        def court_options_ready(drv):
            try:
                # prefer 'CL_court_no' id if present
                try:
                    el = drv.find_element(By.ID, "CL_court_no")
                except Exception:
                    # fallback: any select with name/id containing 'court' but not state/district
                    el = None
                    for s in drv.find_elements(By.TAG_NAME, "select"):
                        nm = (s.get_attribute("name") or "").lower()
                        idn = (s.get_attribute("id") or "").lower()
                        if (
                            "cl_court" in nm
                            or "cl_court" in idn
                            or (
                                "court" in nm
                                and "complex" not in nm
                                and "state" not in nm
                                and "dist" not in nm
                            )
                        ):
                            el = s
                            break
                if not el:
                    return False
                opts = el.find_elements(By.TAG_NAME, "option")
                real = [
                    o
                    for o in opts
                    if (o.get_attribute("value") or "").strip() not in ("", "0", None)
                ]
                return len(real) >= 1
            except Exception:
                return False

        # Retry if court options not found
        # Try up to 3 times: if the popup appears and blocks, close it and re-dispatch change on complex select
        attempts = 0
        while attempts < 3:
            try:
                WebDriverWait(driver, wait_timeout).until(court_options_ready)
                break
            except Exception:
                attempts += 1
                logger.warning(
                    "Court options not ready (attempt %d). Trying to close alerts and re-trigger complex change.",
                    attempts,
                )
                try_close_alerts(driver)
                # re-dispatch complex change to force population
                try:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                        complex_el,
                    )
                except Exception:
                    pass
                time.sleep(0.7)
        else:
            driver.quit()
            raise SystemExit(
                "Timed out waiting for court options. Try increasing wait_timeout or running the browser non-headless."
            )

        # Find the court select element (try id first then heuristics)
        try:
            court_select_el = driver.find_element(By.ID, "CL_court_no")
        except Exception:
            court_select_el = None
            for s in driver.find_elements(By.TAG_NAME, "select"):
                nm = (s.get_attribute("name") or "").lower()
                idn = (s.get_attribute("id") or "").lower()
                if (
                    "cl_court" in nm
                    or "cl_court" in idn
                    or (
                        "court" in nm
                        and "complex" not in nm
                        and "state" not in nm
                        and "dist" not in nm
                    )
                ):
                    court_select_el = s
                    break

        if not court_select_el:
            driver.quit()
            raise SystemExit("Court select element not found; stopping.")

        sel_court = Select(court_select_el)
        chosen_court_opt = None
        for opt in sel_court.options:
            if court_name.strip().lower() in (opt.text or "").strip().lower():
                chosen_court_opt = opt
                break
        if not chosen_court_opt:
            available = [o.text for o in sel_court.options[:40]]
            driver.quit()
            raise SystemExit(
                f"Court '{court_name}' not found. Available (sample): {available}"
            )

        # Select the court and trigger change
        sel_court.select_by_value(chosen_court_opt.get_attribute("value"))
        driver.execute_script(
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            court_select_el,
        )
        logger.info("Selected court: %s", chosen_court_opt.text)
        time.sleep(0.4)
        try_close_alerts(driver)

        # Select the date from the date picker (today)
        if causelist_date:
            try:
                chosen_dt = dateparser.parse(causelist_date, dayfirst=True)
            except Exception:
                raise ValueError(f"Could not parse causelist_date: {causelist_date}")
        else:
            chosen_dt = datetime.now()
        today_str = chosen_dt.strftime("%d-%m-%Y")  # used to fill the date input field
        iso_for_files = chosen_dt.date().isoformat()  # used for filenames below

        try:
            date_input = driver.find_element(By.NAME, "causelist_date")
            date_input.clear()
            date_input.send_keys(today_str)
        except Exception:
            pass

        # 6. Re-fetch captcha for each: Civil and Criminal options.
        # Prompt user, click, wait & save.
        results = []
        for cicri_label in ("Civil", "Criminal"):
            # before each click re-check and close any alert
            try_close_alerts(driver)
            time.sleep(0.25)

            # re-find captcha image & fetch via requests (to carry cookies)
            captcha_src = None
            try:
                wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "img[src*='securimage_show']")
                    ),
                    timeout=10,
                )
            except Exception:
                pass
            try:
                cap_img = driver.find_element(
                    By.CSS_SELECTOR, "img[src*='securimage_show']"
                )
                captcha_src = cap_img.get_attribute("src")
            except Exception:
                captcha_src = None

            cap_path = None
            if captcha_src:
                try:
                    sess = requests.Session()
                    for c in driver.get_cookies():
                        sess.cookies.set(c["name"], c["value"])
                    cap_resp = sess.get(captcha_src, timeout=20)
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    cap_path = os.path.join(
                        out_dir,
                        f"captcha_{chosen_court_opt.get_attribute('value')}_{cicri_label}_{timestamp}.jpg",
                    )
                    with open(cap_path, "wb") as fh:
                        fh.write(cap_resp.content)
                    print("Captcha saved to", cap_path)
                except Exception as e:
                    logger.warning("Failed to fetch captcha via requests: %s", e)
                    cap_path = None
            else:
                logger.warning(
                    "Captcha image not found before clicking %s.", cicri_label
                )

            # find appropriate button freshly
            btn = None
            try:
                # case-insensitive contains
                btn = driver.find_element(
                    By.XPATH,
                    f"//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{cicri_label.lower()}')]",
                )
            except Exception:
                try:
                    btn = driver.find_element(
                        By.XPATH,
                        f"//input[@type='button' and contains(translate(@value,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'{cicri_label.lower()}')]",
                    )
                except Exception:
                    btn = None

            # prompt user to enter captcha
            if cap_path:
                user_captcha = input(
                    f"Enter captcha for court '{chosen_court_opt.text}' ({cicri_label}) [saved at {cap_path}]: "
                ).strip()
            else:
                user_captcha = input(
                    f"Enter captcha for court '{chosen_court_opt.text}' ({cicri_label}): "
                ).strip()

            # fill captcha input (re-find)
            try:
                cap_input = driver.find_element(By.NAME, "cause_list_captcha_code")
                cap_input.clear()
                cap_input.send_keys(user_captcha)
            except Exception:
                logger.warning(
                    "Captcha input field not found; proceeding to click button anyway."
                )

            # click and wait for result
            if btn:
                try:
                    btn.click()
                except Exception:
                    try:
                        driver.execute_script("arguments[0].click();", btn)
                    except Exception as e:
                        logger.error("Failed to click %s button: %s", cicri_label, e)
                        continue
            else:
                logger.warning(
                    "Could not find %s button on page; skipping.", cicri_label
                )
                continue

            # wait for either a pdf or result section (shorter wait)
            try:
                WebDriverWait(driver, max(12, wait_timeout)).until(
                    EC.any_of(
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, "a[href$='.pdf']")
                        ),
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".cause-list-table")
                        ),
                        EC.presence_of_element_located(
                            (By.CSS_SELECTOR, ".cause-list-section")
                        ),
                    )
                )
            except Exception:
                logger.info(
                    "No immediate results for %s (may be empty/blocked).", cicri_label
                )

            # attempt to download pdf first
            saved = None
            try:
                pdf_el = driver.find_element(By.CSS_SELECTOR, "a[href$='.pdf']")
                pdf_url = pdf_el.get_attribute("href")
                sess = requests.Session()
                for c in driver.get_cookies():
                    sess.cookies.set(c["name"], c["value"])
                pdf_resp = sess.get(pdf_url, timeout=30)
                fn = f"causelist_{chosen_court_opt.get_attribute('value')}_{cicri_label}_{iso_for_files}.pdf"
                fnpath = os.path.join(out_dir, fn)
                with open(fnpath, "wb") as fh:
                    fh.write(pdf_resp.content)
                saved = fnpath
                print("Saved PDF to", fnpath)
            except Exception:
                # fallback: scrape HTML content (including multiple tables)
                try:
                    # find the main result container (try several candidates)
                    possible_blocks = driver.find_elements(
                        By.XPATH,
                        "//div[contains(@class,'cause') or contains(.,'In the court of') or contains(.,'Cause Listed on')]",
                    )
                    if not possible_blocks:
                        possible_blocks = driver.find_elements(By.TAG_NAME, "body")

                    # combine all <table> elements found inside result section
                    all_tables_html = ""
                    for block in possible_blocks:
                        tables = block.find_elements(By.TAG_NAME, "table")
                        if tables:
                            for tbl in tables:
                                all_tables_html += tbl.get_attribute("outerHTML") + "\n"

                    # if no tables found, capture full page
                    if not all_tables_html.strip():
                        all_tables_html = driver.page_source

                    fn = f"causelist_{chosen_court_opt.get_attribute('value')}_{cicri_label}_{iso_for_files}.html"
                    fnpath = os.path.join(out_dir, fn)
                    with open(fnpath, "w", encoding="utf-8") as fh:
                        fh.write(all_tables_html)
                    saved = fnpath
                    print(f"✅ Saved cause list HTML (with tables) to {fnpath}")

                    try:
                        json_out = fnpath.replace(".html", ".json")
                        parse_cause_list_html(fnpath, json_out)
                    except Exception as e:
                        logger.warning(f"Auto-parse failed for {fnpath}: {e}")

                except Exception as e:
                    logger.warning(
                        f"⚠️ Failed to save cause-list result for {cicri_label}: {e}"
                    )
                    saved = None

            results.append(
                {"court": chosen_court_opt.text, "cicri": cicri_label, "saved": saved}
            )

            # small pause
            time.sleep(0.5)
            try_close_alerts(driver)

        # save index for this court
        idx_path = os.path.join(
            out_dir,
            f"causelist_index_{chosen_court_opt.get_attribute('value')}_{iso_for_files}.json",
        )
        save_json(results, idx_path)
        print("Index saved to", idx_path)
        return idx_path

    finally:
        try:
            driver.quit()
        except Exception:
            pass


def parse_cause_list_html(
    html_path: str, save_json_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Parse a saved eCourts cause list HTML (e.g. causelist_18^1_Criminal_2025-10-15.html)
    into structured JSON with sections, cases, parties, and advocates.

    Args:
        html_path: Path to the saved HTML file.
        save_json_path: Optional path to save structured JSON.

    Returns:
        dict with {sections: [{section_name, cases:[{sr_no, case_no, party_for, party_against, advocates, raw_text}]}]}
    """
    html_text = Path(html_path).read_text(encoding="utf-8", errors="ignore")
    soup = BeautifulSoup(html_text, "lxml")

    tables = soup.find_all("table", id="dispTable")
    all_sections = []
    section_name = None
    current_cases = []

    def flush_section():
        nonlocal section_name, current_cases
        if section_name and current_cases:
            all_sections.append(
                {
                    "section_name": section_name,
                    "cases": current_cases,
                }
            )
        section_name = None
        current_cases = []

    for tbl in tables:
        for tr in tbl.find_all("tr"):
            # Detect section name
            tds = tr.find_all("td")
            if not tds:
                continue

            # Section title rows (blue text)
            if len(tds) == 1 or ("color:#3880d4" in str(tr)):
                txt = tds[0].get_text(" ", strip=True)
                # skip empty or hr rows
                if txt and txt not in ("-", "hr", "HR"):
                    # flush previous section
                    flush_section()
                    section_name = txt
                    continue

            # Case rows
            if len(tds) >= 4 and tds[0].get_text(strip=True).isdigit():
                sr_no = tds[0].get_text(strip=True)
                case_no = tds[1].get_text(" ", strip=True).replace("View", "").strip()
                party_raw = tds[2].decode_contents().replace("<br>", "\n")
                advocate_raw = tds[3].decode_contents().replace("<br>", "\n")

                # Split 'versus' parties
                party_for = party_against = ""
                parts = re.split(r"\bversus\b", party_raw, flags=re.I)
                if len(parts) == 2:
                    party_for = BeautifulSoup(parts[0], "lxml").get_text(
                        " ", strip=True
                    )
                    party_against = BeautifulSoup(parts[1], "lxml").get_text(
                        " ", strip=True
                    )
                else:
                    party_for = BeautifulSoup(party_raw, "lxml").get_text(
                        " ", strip=True
                    )

                advocates = [
                    adv.strip()
                    for adv in BeautifulSoup(advocate_raw, "lxml")
                    .get_text("\n", strip=True)
                    .split("\n")
                    if adv.strip()
                ]

                current_cases.append(
                    {
                        "sr_no": sr_no,
                        "case_no": case_no,
                        "party_for": party_for,
                        "party_against": party_against,
                        "advocates": advocates,
                        "raw_text": {
                            "party_raw": party_raw.strip(),
                            "advocate_raw": advocate_raw.strip(),
                        },
                    }
                )

    # flush last section
    flush_section()

    data = {"sections": all_sections}
    if save_json_path:
        Path(save_json_path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"✅ Parsed JSON saved to {save_json_path}")

    return data


# endregion Causelist Functions


# region Chapter 8: UI Functions (Streamlit)


# Utility: Input patcher (used for cause-list)
class InputPatcher:
    def __init__(self, value_func):
        self._value_func = value_func
        self._orig_input = builtins.input

    def __enter__(self):
        builtins.input = lambda prompt="": self._value_func(prompt)

    def __exit__(self, exc_type, exc, tb):
        builtins.input = self._orig_input


# Helper: show saved file notification
def notify_saved_path(path: str):
    st.info(
        f"Files saved to `{path}`. Use the sidebar file browser to preview or download them."
    )


def captcha_value_provider(prompt="", fallback_key="ui_captcha_value"):
    """
    Provide captcha text to InputPatcher.
    We avoid creating widgets here. The Streamlit UI should set
    st.session_state[fallback_key] before calling InputPatcher.
    """
    val = ""
    try:
        val = st.session_state.get(fallback_key, "")  # safe even if st not yet used
    except Exception:
        val = ""
    # optionally log the prompt so user sees it in the app area
    try:
        st.write("Library prompt:", prompt)
    except Exception:
        pass
    return val or ""


def human_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def embed_pdf_bytes(data_bytes, height=700):
    b64 = base64.b64encode(data_bytes).decode("utf-8")
    src = f"data:application/pdf;base64,{b64}"
    html = f'<iframe src="{src}" width="100%" height="{height}" type="application/pdf"></iframe>'
    components.html(html, height=height)


def render_html_file(path_obj, height=800):
    html_text = path_obj.read_text(encoding="utf-8", errors="ignore")
    st.warning(
        "Embedded HTML will render static content. Relative assets (CSS/JS) may not load."
    )
    components.html(html_text, height=height, scrolling=True)
    tmp_dir = Path(".") / ".tmp_streamlit"
    tmp_dir.mkdir(exist_ok=True)
    dest = tmp_dir / path_obj.name
    shutil.copy(path_obj, dest)
    st.markdown(f"[Open this HTML in a new tab]({str(dest)})")


def get_all_files(folder: Path):
    return sorted(
        [p for p in folder.rglob("*") if p.is_file()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def file_browser_sidebar(root_dir="outputs"):
    # session keys
    if "fb_last_max_mtime" not in st.session_state:
        st.session_state.fb_last_max_mtime = 0.0
    if "fb_selected_relpath" not in st.session_state:
        st.session_state.fb_selected_relpath = None

    sidebar = st.sidebar
    sidebar.header("Outputs — file browser")

    root_path = Path(root_dir)
    if not root_path.exists():
        sidebar.warning(f"Root folder `{root_dir}` does not exist. Run scraper first.")
        return

    # Subfolder selection
    subfolders = ["/"] + sorted([p.name for p in root_path.iterdir() if p.is_dir()])
    chosen_subfolder = sidebar.selectbox("Subfolder", options=subfolders, index=0)
    folder = root_path if chosen_subfolder == "/" else root_path / chosen_subfolder

    # Gather files recursively
    files = get_all_files(folder)
    sidebar.write(f"Files: {len(files)} (in `{folder}`)")

    # Extensions filter (safe defaults only if present)
    exts = sorted({p.suffix.lower() for p in files if p.suffix})
    preferred_defaults = [".jpg", ".pdf", ".html", ".json", ".txt"]
    default_for_multiselect = [e for e in preferred_defaults if e in exts]
    chosen_exts = sidebar.multiselect(
        "Filter by ext (empty = all)",
        options=exts,
        default=default_for_multiselect,
    )

    # Search filter
    q = sidebar.text_input("Search filename contains", value="")

    # Apply filters
    display_files = files
    if chosen_exts:
        display_files = [p for p in display_files if p.suffix.lower() in chosen_exts]
    if q:
        display_files = [p for p in display_files if q.lower() in p.name.lower()]

    # Build options as POSIX relative paths (works across OSes)
    options = [p.relative_to(folder).as_posix() for p in display_files]

    if not options:
        sidebar.info("No matching files. Try different filters or click Refresh now.")
    else:
        # show single Select file dropdown
        default_idx = 0
        if st.session_state.fb_selected_relpath in options:
            try:
                default_idx = options.index(st.session_state.fb_selected_relpath)
            except ValueError:
                default_idx = 0
        chosen_rel = sidebar.selectbox(
            "Select file", options=options, index=default_idx, key="fb_selectbox"
        )
        # store selection (POSIX string)
        st.session_state.fb_selected_relpath = chosen_rel
        sel_path = folder / Path(chosen_rel)  # safe on Windows/Unix

        # Show file meta and actions vertically (exactly one file)
        sidebar.markdown("---")
        sidebar.write(f"**{chosen_rel}**")
        sidebar.write(
            f"{human_size(sel_path.stat().st_size)} — {time.ctime(sel_path.stat().st_mtime)}"
        )

        # Build safe keys for widgets
        import urllib.parse

        safe_rel = urllib.parse.quote_plus(chosen_rel)

        # Preview button (opens preview in main area)
        if sidebar.button("👁️ Preview", key=f"preview_{safe_rel}"):
            # we already set fb_selected_relpath above; rerun to show preview in main area
            st.rerun()
        sidebar.caption("Preview opens in main area with download option.")

    # Small controls & auto-refresh
    sidebar.markdown("---")
    sidebar.subheader("Auto-refresh")
    auto = sidebar.checkbox(
        "Enable auto-refresh (polling)",
        value=False,
        help="When enabled the page will re-run every `interval` seconds.",
    )
    interval = sidebar.number_input(
        "Interval (seconds)", min_value=3, max_value=600, value=10, step=1
    )
    if sidebar.button("Refresh now"):
        st.rerun()

    # mtime tracking for quick-detection of new files
    cur_max = 0.0
    if files:
        cur_max = max(p.stat().st_mtime for p in files)
    if st.session_state.fb_last_max_mtime == 0.0:
        st.session_state.fb_last_max_mtime = cur_max

    if auto:
        if cur_max > st.session_state.fb_last_max_mtime:
            st.session_state.fb_last_max_mtime = cur_max
            st.rerun()
        time.sleep(interval)
        st.rerun()

    # MAIN AREA — preview will appear here when fb_selected_relpath is set
    st.divider()
    st.header("File Preview Section")
    if (
        "fb_selected_relpath" not in st.session_state
        or st.session_state.fb_selected_relpath is None
    ):
        st.info("Select a file from the sidebar to preview.")
        return

    sel_path = folder / Path(st.session_state.fb_selected_relpath)
    if not sel_path.exists():
        st.error(
            "Selected file not found (it may have been moved/deleted). Click Refresh now."
        )
        return

    st.write("Selected:", sel_path)
    st.write(
        "Size:",
        human_size(sel_path.stat().st_size),
        " — Modified:",
        time.ctime(sel_path.stat().st_mtime),
    )

    with open(sel_path, "rb") as f:
        data = f.read()
    st.download_button("⬇️ Download file", data, file_name=sel_path.name)

    mime, _ = mimetypes.guess_type(str(sel_path))
    if mime is None:
        mime = "application/octet-stream"

    if mime.startswith("image/"):
        st.image(data, caption=sel_path.name)
    elif mime == "application/pdf" or sel_path.suffix.lower() == ".pdf":
        embed_pdf_bytes(data)
    elif sel_path.suffix.lower() in [".html", ".htm"]:
        render_html_file(sel_path)
    elif sel_path.suffix.lower() in [".txt", ".log", ".py", ".csv", ".json"]:
        try:
            text = data.decode("utf-8")
        except Exception:
            text = str(data)
        if sel_path.suffix.lower() == ".json":
            try:
                st.json(json.loads(text))
            except Exception:
                st.code(text)
        else:
            st.code(text, language="text")
    else:
        st.write("Preview not available. Use the Download button.")


# endregion UI Functions


# region ---------- Chapter 9: Causelist Helper Functions for Streamlit App ----------


def prepare_causelist_request(
    session: requests.Session,
    state: str,
    district: str,
    court_complex: Optional[str],
    court_name: Optional[str],
    out_dir: str = "outputs/causelists",
    causelist_date: Optional[str] = None,
):
    """
    Phase 1: navigate and prepare payloads for Civil/Criminal, download captcha images (if shown)
    Returns: dict {
        'out_dir': out_dir,
        'payload_template': {common form fields without cicri or captcha},
        'cicri_list': ['civ','cri'],
        'captcha_paths': {'civ': '/path/to/cap.jpg' or None, 'cri': ...},
        'state_code': ...,
        'court_opt': {...}
    }
    This function will NOT submit the cause-list. It only fetches the page(s) and saves any captcha images.
    """
    os.makedirs(out_dir, exist_ok=True)

    # Borrow the initial page parsing code from download_entire_cause_list
    resp = session.get(
        CAUSE_LIST_PAGE, headers={"User-Agent": BASE_HEADERS["User-Agent"]}, timeout=30
    )
    resp.raise_for_status()
    soup = make_soup(resp.text)

    # parse candidate selects/options
    states = _parse_select_options(
        soup, ["sess_state_code", "state_code", "state", "ddl_state_code", "state_name"]
    )
    dists = _parse_select_options(
        soup,
        ["sess_dist_code", "dist_code", "district_code", "district", "ddl_dist_code"],
    )
    complexes = _parse_select_options(soup, ["court_complex_code", "court_complex"])
    courts = _parse_select_options(
        soup, ["CL_court_no", "court_name", "court_name_txt"]
    )

    # Resolve inputs to codes (re-use your _resolve_name_or_code)
    state_opt = _resolve_name_or_code(states, state)
    if not state_opt:
        raise SystemExit(f"Could not resolve state '{state}'.")

    dist_opt = _resolve_name_or_code(dists, district)
    if not dist_opt:
        raise SystemExit(f"Could not resolve district '{district}'.")

    state_code = state_opt["value"]
    dist_code = dist_opt["value"]

    # POST to populate complexes/courts if needed
    try:
        state_field_name = "state_code"
        for cand in [
            "sess_state_code",
            "state_code",
            "state",
            "ddl_state_code",
            "state_name",
        ]:
            el = soup.find("select", {"name": cand}) or soup.find(
                "select", {"id": cand}
            )
            if el:
                state_field_name = el.get("name") or el.get("id") or state_field_name
                break
        r2 = session.post(
            CAUSE_LIST_PAGE,
            data={state_field_name: state_code},
            headers={"User-Agent": BASE_HEADERS["User-Agent"]},
            timeout=20,
        )
        if r2.ok:
            soup2 = make_soup(r2.text)
            complexes = (
                _parse_select_options(soup2, ["court_complex_code", "court_complex"])
                or complexes
            )
            courts = (
                _parse_select_options(
                    soup2, ["CL_court_no", "court_name", "court_name_txt"]
                )
                or courts
            )
            soup = soup2
    except Exception:
        pass

    complex_opt = _resolve_name_or_code(complexes, court_complex)
    if not complex_opt:
        raise SystemExit(f"Court complex '{court_complex}' not found.")

    # obtain courts by posting complex if needed
    try:
        r3 = session.post(
            CAUSE_LIST_PAGE,
            data={
                "state_code": state_code,
                "dist_code": dist_code,
                "court_complex_code": complex_opt["value"],
            },
            headers={"User-Agent": BASE_HEADERS["User-Agent"]},
            timeout=20,
        )
        if r3.ok:
            s3 = make_soup(r3.text)
            courts = (
                _parse_select_options(
                    s3, ["CL_court_no", "court_name", "court_name_txt"]
                )
                or courts
            )
    except Exception:
        pass

    court_opt = _resolve_name_or_code(courts, court_name)
    if not court_opt:
        raise SystemExit(f"Court '{court_name}' not found.")

    # chosen date
    if causelist_date:
        chosen_dt = dateparser.parse(causelist_date, dayfirst=True)
    else:
        chosen_dt = datetime.now()
    today = chosen_dt.strftime("%d-%m-%Y")

    # Build the common payload template (without cicri and without captcha)
    payload_template = {
        "state_code": state_code,
        "dist_code": dist_code,
        "court_complex_code": complex_opt["value"],
        "CL_court_no": court_opt["value"],
        "court_name_txt": court_opt["text"],
        "causelist_date": today,
        "est_code": "",
    }

    # Now fetch captcha images for each cicri (server might show different captchas per click)
    captcha_paths = {}
    for cicri in ("civ", "cri"):
        # request the page that has captcha — often the page is same; we attempt to find securimage img in current soup
        cap_src = None
        for i in soup.find_all("img"):
            src = i.get("src", "")
            if "securimage_show" in src:
                cap_src = src
                break
        cap_path = None
        if cap_src:
            try:
                # make absolute url if needed
                if not cap_src.startswith("http"):
                    cap_url = urljoin(BASE_URL, cap_src)
                else:
                    cap_url = cap_src
                # reuse session to download
                r = session.get(cap_url, timeout=20)
                r.raise_for_status()
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                cap_path = os.path.join(
                    out_dir, f"captcha_{court_opt['value']}_{cicri}_{timestamp}.jpg"
                )
                with open(cap_path, "wb") as fh:
                    fh.write(r.content)
            except Exception:
                cap_path = None
        captcha_paths[cicri] = cap_path
        # small pause; server may rotate captcha on actual button click; this is best-effort
        time.sleep(0.2)

    return {
        "out_dir": out_dir,
        "payload_template": payload_template,
        "cicri_list": ["civ", "cri"],
        "captcha_paths": captcha_paths,
        "court": court_opt,
        "date": today,
    }


def submit_causelist_attempt(
    session: requests.Session,
    payload_template: dict,
    cicri: str,
    captcha_value: str,
    out_dir: str,
    max_retries_on_popup: int = 3,
):
    """
    Submit a single cicri ('civ' or 'cri') using the given payload_template and captcha value.
    Returns saved path (pdf/html) or None.
    """
    payload = payload_template.copy()
    payload["cicri"] = cicri
    payload["cause_list_captcha_code"] = captcha_value or ""
    attempt = 0
    saved = None
    while attempt < max_retries_on_popup:
        attempt += 1
        try:
            resp = session.post(
                CAUSE_LIST_SUBMIT,
                data=payload,
                headers={
                    "User-Agent": BASE_HEADERS["User-Agent"],
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            time.sleep(0.6 * attempt)
            continue

        # try JSON -> pdf
        try:
            j = resp.json()
            pdf_url = (
                j.get("pdfUrl")
                or j.get("pdf_url")
                or j.get("cause_list_pdf")
                or j.get("pdf")
            )
            if pdf_url:
                if not pdf_url.startswith("http"):
                    pdf_url = urljoin(BASE_URL, pdf_url)
                outfn = os.path.join(
                    out_dir,
                    f"causelist_{payload_template['CL_court_no']}_{cicri}_{payload_template['causelist_date'].replace('-', '')}.pdf",
                )
                download_file(pdf_url, outfn)
                saved = outfn
                return saved
        except Exception:
            pass

        # html path
        sresp = make_soup(resp.text)
        a_pdf = sresp.find("a", href=lambda h: h and h.lower().endswith(".pdf"))
        if a_pdf:
            pdf_url = a_pdf["href"]
            if not pdf_url.startswith("http"):
                pdf_url = urljoin(BASE_URL, pdf_url)
            outfn = os.path.join(
                out_dir,
                f"causelist_{payload_template['CL_court_no']}_{cicri}_{payload_template['causelist_date'].replace('-', '')}.pdf",
            )
            try:
                download_file(pdf_url, outfn)
                saved = outfn
                return saved
            except Exception:
                saved = None

        # fallback: extract tables and save html
        try:
            tables = sresp.find_all("table")
            all_tables_html = ""
            for tbl in tables:
                all_tables_html += str(tbl) + "\n"
            if not all_tables_html.strip():
                all_tables_html = (
                    sresp.body.decode_contents() if sresp.body else resp.text
                )
            fn = os.path.join(
                out_dir,
                f"causelist_{payload_template['CL_court_no']}_{cicri}_{payload_template['causelist_date'].replace('-', '')}.html",
            )
            with open(fn, "w", encoding="utf-8") as fh:
                fh.write(all_tables_html)
            saved = fn
            # try parse to json
            try:
                json_out = fn.replace(".html", ".json")
                parse_cause_list_html(fn, json_out)
            except Exception:
                pass
            return saved
        except Exception:
            time.sleep(0.6 * attempt)
            continue

    return saved


def selenium_prepare_causelist(
    state,
    district,
    court_complex,
    court_name,
    causelist_date=None,
    out_dir="outputs/causelists",
    headless=False,
    wait_timeout=30,
):
    """
    Selenium prepare.
    - navigates to cause list page
    - selects state -> district -> complex -> court (with retries & explicit change events)
    - captures captcha images for Civil and Criminal by clicking the page buttons
    - returns: {"cookies": [...], "captcha_paths": {"civ":path, "cri":path}, "out_dir": out_dir}
    """
    os.makedirs(out_dir, exist_ok=True)

    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1400,1200")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--log-level=3")

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    wait = WebDriverWait(driver, wait_timeout)

    def try_close_alerts(drv):
        """Try to dismiss modal/popup overlays that might block interaction."""
        try:
            close_selectors = [
                "button[aria-label='Close']",
                "button.close",
                "button.btn-close",
                ".swal2-close",
                ".validationError .close",
                ".modal .close",
            ]
            for sel in close_selectors:
                try:
                    els = drv.find_elements(By.CSS_SELECTOR, sel)
                    for e in els:
                        try:
                            if e.is_displayed():
                                drv.execute_script("arguments[0].click();", e)
                                time.sleep(0.25)
                        except Exception:
                            pass
                except Exception:
                    pass
            # remove overlays by JS as last resort
            try:
                drv.execute_script(
                    """
                    Array.from(document.querySelectorAll('.modal-backdrop, .modal, .overlay, .sweet-alert, .validationError, .popup, .ui-widget-overlay')).forEach(n => { try{ n.parentNode && n.parentNode.removeChild(n);}catch(e){}});
                    document.body && document.body.classList && document.body.classList.remove('modal-open');
                    """
                )
            except Exception:
                pass
            time.sleep(0.2)
        except Exception:
            pass

    def dispatch_change(el):
        """Dispatch change event so page JS runs."""
        try:
            driver.execute_script(
                "var e = document.createEvent('HTMLEvents'); e.initEvent('change', true, false); arguments[0].dispatchEvent(e);",
                el,
            )
        except Exception:
            try:
                driver.execute_script(
                    "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                    el,
                )
            except Exception:
                pass

    def find_select_by_hint(hints):
        """Return a <select> element whose name/id contains any hint; fallback to a large select."""
        for s in driver.find_elements(By.TAG_NAME, "select"):
            try:
                name = (s.get_attribute("name") or "").lower()
                _id = (s.get_attribute("id") or "").lower()
                if any(h in name for h in hints) or any(h in _id for h in hints):
                    return s
            except Exception:
                continue
        # fallback - select with many options
        for s in driver.find_elements(By.TAG_NAME, "select"):
            try:
                opts = s.find_elements(By.TAG_NAME, "option")
                if len(opts) > 8:
                    return s
            except Exception:
                continue
        return None

    def wait_for_select_options(select_el, min_options=2, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                opts = select_el.find_elements(By.TAG_NAME, "option")
                valid = [
                    o
                    for o in opts
                    if (o.get_attribute("value") or "").strip() not in ("", "0", None)
                ]
                if len(valid) >= min_options:
                    return True
            except Exception:
                pass
            try_close_alerts(driver)
            time.sleep(0.3)
        return False

    def select_option(select_el, user_text):
        """Select by substring of visible text, then by visible text, then by exact value."""
        from selenium.webdriver.support.ui import Select

        S = Select(select_el)
        # substring visible text
        for o in S.options:
            try:
                if user_text.strip().lower() in (o.text or "").strip().lower():
                    S.select_by_value(o.get_attribute("value"))
                    dispatch_change(select_el)
                    return True
            except Exception:
                continue
        # visible text exact
        try:
            S.select_by_visible_text(user_text)
            dispatch_change(select_el)
            return True
        except Exception:
            pass
        # try by value exact
        for o in S.options:
            try:
                if (o.get_attribute("value") or "") == str(user_text).strip():
                    S.select_by_value(o.get_attribute("value"))
                    dispatch_change(select_el)
                    return True
            except Exception:
                continue
        return False

    def capture_captcha_for_label(label_text, key_label):
        """Click the Civil/Criminal button, wait for captcha img and save robustly."""
        try_close_alerts(driver)
        time.sleep(0.25)
        btn = None
        try:
            btn = driver.find_element(
                By.XPATH,
                f"//button[contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{label_text.lower()}')]",
            )
        except Exception:
            try:
                btn = driver.find_element(
                    By.XPATH,
                    f"//input[@type='button' and contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'), '{label_text.lower()}')]",
                )
            except Exception:
                btn = None

        if btn:
            try:
                # JS click to avoid interception
                driver.execute_script("arguments[0].click();", btn)
            except Exception:
                try:
                    btn.click()
                except Exception:
                    pass

        # wait for captcha image
        img_el = None
        try:
            img_el = wait.until(
                EC.presence_of_element_located(
                    (
                        By.XPATH,
                        "//img[contains(@src,'securimage') or contains(@src,'captcha') or contains(@class,'captcha')]",
                    )
                )
            )
        except Exception:
            try:
                img_el = driver.find_element(
                    By.XPATH,
                    "//label[contains(translate(., 'CAPTCHA','captcha'),'captcha')]/following::img[1]",
                )
            except Exception:
                img_el = None

        if not img_el:
            logger.warning(
                "[selenium_prepare] ⚠️ No captcha image found after clicking %s",
                label_text,
            )
            return None

        ts = str(int(time.time()))
        fname = os.path.join(out_dir, f"captcha_{key_label}_{ts}.png")
        # try element.screenshot first
        try:
            img_el.screenshot(fname)
            logger.info("[selenium_prepare] ✓ Saved %s captcha: %s", label_text, fname)
            return fname
        except Exception:
            try:
                png = driver.get_screenshot_as_png()
                im = Image.open(BytesIO(png))
                rect = driver.execute_script(
                    "var r = arguments[0].getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height, dpr: window.devicePixelRatio || 1};",
                    img_el,
                )
                dpr = rect.get("dpr", 1) or 1
                x = int(round(rect["x"] * dpr))
                y = int(round(rect["y"] * dpr))
                w = int(round(rect["w"] * dpr))
                h = int(round(rect["h"] * dpr))
                left = max(0, x)
                upper = max(0, y)
                right = min(im.size[0], left + w)
                lower = min(im.size[1], upper + h)
                if right <= left or lower <= upper:
                    with open(fname, "wb") as fh:
                        fh.write(png)
                    logger.info(
                        "[selenium_prepare] ✓ Saved fullpage fallback captcha: %s",
                        fname,
                    )
                    return fname
                cropped = im.crop((left, upper, right, lower))
                cropped.save(fname)
                logger.info(
                    "[selenium_prepare] ✓ Saved %s captcha (cropped): %s",
                    label_text,
                    fname,
                )
                return fname
            except Exception as e:
                logger.warning(
                    "[selenium_prepare] ⚠️ Failed to capture captcha image: %s", e
                )
                return None

    cookies = []
    captcha_paths = {"civ": None, "cri": None}
    try:
        # Start with explicit about:blank to avoid 'data:' initial state
        try:
            driver.get("about:blank")
            time.sleep(0.15)
        except Exception:
            pass

        driver.get(CAUSE_LIST_PAGE)
        time.sleep(0.6)
        try_close_alerts(driver)

        # wait until at least one select exists on the real page
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "select")))

        # -------- State --------
        state_sel = find_select_by_hint(["state", "sess_state", "sess_state_code"])
        if not state_sel:
            logger.error("[selenium_prepare] State select not found on page.")
            raise SystemExit("State select not found.")
        if not select_option(state_sel, state):
            logger.error("[selenium_prepare] Could not select state '%s'", state)
            raise SystemExit(f"Could not select state '{state}'")
        logger.info("[selenium_prepare] ✓ Selected state: %s", state)
        time.sleep(0.6)
        try_close_alerts(driver)

        # -------- District --------
        district_sel = find_select_by_hint(["dist", "district", "sess_dist"])
        if district_sel and not wait_for_select_options(
            district_sel, min_options=2, timeout=wait_timeout
        ):
            logger.warning(
                "[selenium_prepare] ⚠️ District select not ready - retrying (increase wait_timeout if slow)"
            )
        district_sel = find_select_by_hint(["dist", "district", "sess_dist"])
        if not district_sel:
            logger.error("[selenium_prepare] District select not found.")
            raise SystemExit("District select not found.")
        if not select_option(district_sel, district):
            logger.error("[selenium_prepare] Could not select district '%s'", district)
            raise SystemExit(f"Could not select district '{district}'")
        logger.info("[selenium_prepare] ✓ Selected dist: %s", district)
        time.sleep(0.6)
        try_close_alerts(driver)

        # -------- Complex --------
        complex_sel = find_select_by_hint(["complex", "court_complex", "courtcomplex"])
        if complex_sel and not wait_for_select_options(
            complex_sel, min_options=2, timeout=wait_timeout
        ):
            logger.warning("[selenium_prepare] ⚠️ Complex select not ready - continuing")
        complex_sel = find_select_by_hint(["complex", "court_complex", "courtcomplex"])
        if not complex_sel:
            logger.error("[selenium_prepare] Complex select not found.")
            raise SystemExit("Court complex select not found.")
        if not select_option(complex_sel, court_complex):
            logger.error(
                "[selenium_prepare] Could not select court complex '%s'", court_complex
            )
            raise SystemExit(f"Could not select court complex '{court_complex}'")
        logger.info("[selenium_prepare] ✓ Selected complex: %s", court_complex)
        time.sleep(0.8)
        try_close_alerts(driver)

        # -------- Court Name --------
        court_sel = None
        tries = 0
        while tries < 5:
            court_sel = find_select_by_hint(
                ["cl_court", "court", "CL_court_no", "court_name"]
            )
            if court_sel and wait_for_select_options(
                court_sel, min_options=2, timeout=6
            ):
                break
            tries += 1
            logger.warning(
                "[selenium_prepare] ⚠️ Court select not ready, retrying (%d/5)", tries
            )
            try_close_alerts(driver)
            # re-dispatch change on complex to trigger population
            try:
                dispatch_change(complex_sel)
            except Exception:
                pass
            time.sleep(0.6)
        if not court_sel:
            logger.warning(
                "[selenium_prepare] ⚠️ Could not locate court select element (will continue and try to capture captchas anyway)."
            )
        else:
            if not select_option(court_sel, court_name):
                logger.warning(
                    "[selenium_prepare] ⚠️ Could not select court (first attempt). Will continue and try again before each capture."
                )
            else:
                logger.info("[selenium_prepare] ✓ Selected court: %s", court_name)

        # set date if present
        if causelist_date:
            try:
                date_input = None
                for cand in ["causelist_date", "cause_list_date", "CauseListDate"]:
                    try:
                        date_input = driver.find_element(By.NAME, cand)
                        break
                    except Exception:
                        pass
                if date_input:
                    date_input.clear()
                    date_input.send_keys(causelist_date)
                    dispatch_change(date_input)
                    logger.info("[selenium_prepare] Date set: %s", causelist_date)
            except Exception:
                pass

        # capture Civil
        logger.info("[selenium_prepare] → Capturing Civil captcha")
        try:
            fresh_court_sel = find_select_by_hint(
                ["cl_court", "court", "CL_court_no", "court_name"]
            )
            if fresh_court_sel:
                select_option(fresh_court_sel, court_name)
        except Exception:
            pass
        captcha_paths["civ"] = capture_captcha_for_label("Civil", "civ")
        time.sleep(0.4)
        try_close_alerts(driver)

        # reload page and re-select so criminal captcha is fresh
        try:
            cur = driver.current_url
            driver.get(cur)
            time.sleep(0.7)
            try_close_alerts(driver)
            # re-select selects
            try:
                state_sel = find_select_by_hint(["state", "sess_state"])
                if state_sel:
                    select_option(state_sel, state)
                district_sel = find_select_by_hint(["dist", "district"])
                if district_sel:
                    select_option(district_sel, district)
                complex_sel = find_select_by_hint(["complex", "court_complex"])
                if complex_sel:
                    select_option(complex_sel, court_complex)
                court_sel = find_select_by_hint(
                    ["cl_court", "court", "CL_court_no", "court_name"]
                )
                if court_sel:
                    select_option(court_sel, court_name)
            except Exception:
                pass
        except Exception:
            pass

        logger.info("[selenium_prepare] → Capturing Criminal captcha")
        captcha_paths["cri"] = capture_captcha_for_label("Criminal", "cri")
        try_close_alerts(driver)

        cookies = driver.get_cookies()
        logger.info(
            "[selenium_prepare] Preparation complete. Captchas: %s", captcha_paths
        )
        return {"cookies": cookies, "captcha_paths": captcha_paths, "out_dir": out_dir}
    except Exception as e:
        logger.exception("[selenium_prepare] Exception during prepare: %s", e)
        raise
    finally:
        try:
            driver.quit()
        except Exception:
            pass


# endregion Causelist Helper Functions for Streamlit App
