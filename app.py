# app.py
import streamlit as st
from datetime import date
from pathlib import Path
import requests
import json
import time

# Import functions (from your functions.py)
from functions import (
    _get_app_token_and_captcha,
    _post_search,
    parse_eCourts_response,
    download_entire_cause_list,
    download_entire_cause_list_selenium,
    generate_text_report,
    InputPatcher,
    notify_saved_path,
    captcha_value_provider,
    human_size,
    embed_pdf_bytes,
    render_html_file,
    get_all_files,
    file_browser_sidebar,
    prepare_causelist_request,
    submit_causelist_attempt,
    selenium_prepare_causelist,
)

# Page config
st.set_page_config(
    page_title="eCourts Scraper (Streamlit UI)",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.title("eCourts Scraper by Sumiet")
st.markdown(
    "This app mirrors the CLI behaviour in `script.py` using the existing functions in `functions.py`."
)

st.divider()
tab = st.radio(
    "Choose action",
    ["CNR lookup (single CNR)", "Download Cause List (requests/selenium)"],
)

CNR_OUTDIR = Path("outputs") / "cnr"
CNR_OUTDIR.mkdir(parents=True, exist_ok=True)

st.divider()
if tab == "CNR lookup (single CNR)":
    st.header("CNR lookup (single CNR)")
    st.markdown("Enter a CNR, fetch captcha, type the captcha, then submit the search.")

    if "cnr_input" not in st.session_state:
        st.session_state.cnr_input = ""
    st.session_state.cnr_input = st.text_input(
        "Enter CNR (example: MHPU050000272025)",
        value=st.session_state.cnr_input,
        key="cnr_input_field",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        if st.button("Fetch captcha", key="fetch_captcha"):
            if not st.session_state.cnr_input.strip():
                st.error("Please enter a CNR before fetching captcha.")
            else:
                sess = requests.Session()
                try:
                    st.session_state._cnr_session = sess
                    st.session_state._cnr_info = _get_app_token_and_captcha(sess)
                    st.success("Captcha fetched succesfully!")
                except Exception as e:
                    st.exception(e)
    with col2:
        st.write("CNR outputs will be saved to:")
        st.code(str(CNR_OUTDIR))

    if "_cnr_info" in st.session_state:
        info = st.session_state._cnr_info or {}
        captcha_path = info.get("captcha_path")
        app_token = info.get("app_token")
        if captcha_path and Path(captcha_path).exists():
            st.image(captcha_path)
        else:
            st.info(
                "No captcha image found or downloaded; you may still submit (leave captcha empty)."
            )

        if "cnr_captcha_value" not in st.session_state:
            st.session_state.cnr_captcha_value = ""
        st.session_state.cnr_captcha_value = st.text_input(
            "Enter captcha",
            value=st.session_state.cnr_captcha_value,
            key="cnr_captcha_field",
        )

        if st.button("Submit and search", key="submit_cnr"):
            if ("_cnr_session" not in st.session_state) or (
                "_cnr_info" not in st.session_state
            ):
                st.error(
                    "Session or captcha token missing — click 'Fetch captcha' first."
                )
            else:
                sess = st.session_state._cnr_session
                app_token = st.session_state._cnr_info.get("app_token")
                captcha_val = st.session_state.cnr_captcha_value.strip()
                cnr = st.session_state.cnr_input.strip()
                with st.spinner("Posting search to eCourts..."):
                    try:
                        resp = _post_search(
                            sess,
                            cino=cnr,
                            fcaptcha_code=captcha_val,
                            app_token=app_token,
                        )
                        if resp is None:
                            st.error(
                                "Network error during POST. Check your connection or try again."
                            )
                        else:
                            parsed = parse_eCourts_response(resp)
                            parsed["session_cookies"] = sess.cookies.get_dict()
                            notify_saved_path(str(CNR_OUTDIR))
                            CNR_OUTDIR.mkdir(parents=True, exist_ok=True)
                            json_path = CNR_OUTDIR / f"{cnr}.json"
                            with open(json_path, "w", encoding="utf-8") as fh:
                                json.dump(parsed, fh, ensure_ascii=False, indent=2)
                            st.success(f"Json generated: `{json_path}`")

                            try:
                                txt_path = CNR_OUTDIR / f"{cnr}_report.txt"
                                generate_text_report(parsed, str(txt_path))
                                st.success(f"Text report generated: `{txt_path}`")
                                with open(txt_path, "rb") as _f:
                                    rpt_bytes = _f.read()
                                st.download_button(
                                    "Download text report",
                                    rpt_bytes,
                                    file_name=txt_path.name,
                                )
                            except Exception as e:
                                st.warning(f"Could not generate text report: {e}")
                    except Exception as e:
                        st.exception(e)


# ---------- CAUSELIST FLOW (kept mostly same; we will polish later) ----------
elif tab == "Download Cause List (requests/selenium)":
    st.header("Download Cause List (single court)")
    st.info("Note: Outputs are saved to: `outputs/causelists`")

    # --- form inputs ---
    col_a, col_b = st.columns(2)
    with col_a:
        mode = st.selectbox("Mode (selenium recommended)", ["requests", "selenium"])
        state = st.text_input("State (name or code)", value="")
        district = st.text_input("District (name or code)", value="")
        court_complex = st.text_input("Court Complex (visible text)", value="")
        court_name = st.text_input("Court Name (visible text)", value="")
        causelist_date = st.date_input("Cause list date", value=date.today())
        st.write("Cause-list files will be saved under `outputs/causelists`.")
        headless = st.checkbox(
            "Selenium headless (only used in selenium mode)", value=False
        )
        wait_timeout = st.number_input(
            "Selenium wait timeout (seconds)", min_value=10, max_value=120, value=30
        )
    with col_b:
        st.markdown("**Notes / captcha**")
        st.markdown(
            """
- For `requests` mode the library will attempt to download a captcha and we will present it for you to solve.  
- For `selenium` mode the app uses Selenium to generate captcha images (so JS/server-side code runs correctly).  
- After the Prepare step you will see per-type captchas (Civil and Criminal). Submit them one-by-one to download results.
"""
        )

    DEFAULT_CAUSELIST_OUTDIR = "outputs/causelists"
    Path(DEFAULT_CAUSELIST_OUTDIR).mkdir(parents=True, exist_ok=True)

    # Prepare step
    if st.button("Run Download Script"):
        # validation
        if not (state and district and court_complex and court_name):
            st.error("Please fill state, district, court_complex and court_name.")
        else:
            st.info(f"Starting cause-list download (mode={mode})")
            # clear previous state keys
            for k in [
                "causelist_prep",
                "causelist_session",
                "causelist_payload_template",
                "last_causelist_saved",
                "last_causelist_type",
                "civ_captcha",
                "cri_captcha",
            ]:
                if k in st.session_state:
                    del st.session_state[k]

            if mode == "requests":
                sess = requests.Session()
                try:
                    with st.spinner(
                        "Preparing (requests): fetching page and captcha images..."
                    ):
                        prep = prepare_causelist_request(
                            session=sess,
                            state=state,
                            district=district,
                            court_complex=court_complex,
                            court_name=court_name,
                            out_dir=DEFAULT_CAUSELIST_OUTDIR,
                            causelist_date=causelist_date.strftime("%d-%m-%Y"),
                        )
                    st.session_state["causelist_session"] = sess
                    st.session_state["causelist_prep"] = prep
                    if prep and prep.get("payload_template"):
                        st.session_state["causelist_payload_template"] = prep.get(
                            "payload_template"
                        )
                    st.success(
                        "Preparation finished. Captcha images saved; you can now submit Civil, then Criminal."
                    )
                except Exception as e:
                    st.exception(e)
                    st.error("Prepare step failed. See error above.")
            else:
                # selenium prepare
                try:
                    with st.spinner(
                        "Starting Selenium prepare: opening browser and capturing captcha images..."
                    ):
                        # IMPORTANT: pass headless and wait_timeout from UI
                        prep = selenium_prepare_causelist(
                            state=state,
                            district=district,
                            court_complex=court_complex,
                            court_name=court_name,
                            causelist_date=causelist_date.strftime("%d-%m-%Y"),
                            out_dir=DEFAULT_CAUSELIST_OUTDIR,
                            headless=bool(headless),
                            wait_timeout=int(wait_timeout),
                        )
                    if not prep:
                        st.error("Selenium prepare returned nothing.")
                    else:
                        st.session_state["causelist_prep"] = prep
                        sess = requests.Session()
                        # load cookies returned by selenium so subsequent requests mimic the session
                        for c in prep.get("cookies", []) or []:
                            try:
                                name = c.get("name")
                                value = c.get("value")
                                domain = c.get("domain", None)
                                if name and value:
                                    sess.cookies.set(name, value, domain=domain)
                            except Exception:
                                pass
                        st.session_state["causelist_session"] = sess
                        if prep.get("payload_template"):
                            st.session_state["causelist_payload_template"] = prep.get(
                                "payload_template"
                            )
                        st.success(
                            "Preparation finished. Captcha images saved; cookies captured."
                        )
                except Exception as e:
                    st.exception(e)
                    st.error("Selenium prepare failed. See error above.")

    # If not prepared yet, show hint
    if "causelist_prep" not in st.session_state:
        st.info(
            "Click **Run Download Script** to prepare captchas (then solve Civil, then Criminal)."
        )
    else:
        prep = st.session_state["causelist_prep"]
        captcha_paths = prep.get("captcha_paths", {}) if isinstance(prep, dict) else {}
        out_dir = (
            prep.get("out_dir", DEFAULT_CAUSELIST_OUTDIR)
            if isinstance(prep, dict)
            else DEFAULT_CAUSELIST_OUTDIR
        )
        sess = st.session_state.get("causelist_session", requests.Session())
        payload_template = st.session_state.get("causelist_payload_template", {}) or {}

        st.session_state.setdefault("civ_captcha", "")
        st.session_state.setdefault("cri_captcha", "")

        st.markdown("### Civil")
        civ_path = captcha_paths.get("civ")
        if civ_path and Path(civ_path).exists():
            st.image(civ_path, caption="Captcha for Civil")
        else:
            st.warning("Civil captcha not available — re-run Prepare if needed.")

        st.session_state["civ_captcha"] = st.text_input(
            "Enter Civil captcha",
            value=st.session_state["civ_captcha"],
            key="ui_civ_cap",
        )
        if st.button("Submit Civil captcha and download", key="submit_civ"):
            try:
                with st.spinner("Submitting Civil..."):
                    saved = submit_causelist_attempt(
                        sess,
                        payload_template=payload_template,
                        cicri="civ",
                        captcha_value=st.session_state.get("civ_captcha", "").strip(),
                        out_dir=out_dir,
                    )
                if saved:
                    st.success(f"Civil saved: {saved}")
                    st.session_state["last_causelist_saved"] = str(saved)
                    st.session_state["last_causelist_type"] = "civil"
                else:
                    st.error(
                        "Civil download failed — possibly invalid captcha. Re-run Prepare to refresh captchas."
                    )
            except Exception as e:
                st.exception(e)
                st.error("Error during Civil submit. See trace above.")

        st.markdown("---")
        st.markdown("### Criminal")
        cri_path = captcha_paths.get("cri")
        if cri_path and Path(cri_path).exists():
            st.image(cri_path, caption="Captcha for Criminal")
        else:
            st.warning("Criminal captcha not available — re-run Prepare if needed.")

        st.session_state["cri_captcha"] = st.text_input(
            "Enter Criminal captcha",
            value=st.session_state["cri_captcha"],
            key="ui_cri_cap",
        )
        if st.button("Submit Criminal captcha and download", key="submit_cri"):
            try:
                with st.spinner("Submitting Criminal..."):
                    saved = submit_causelist_attempt(
                        sess,
                        payload_template=payload_template,
                        cicri="cri",
                        captcha_value=st.session_state.get("cri_captcha", "").strip(),
                        out_dir=out_dir,
                    )
                if saved:
                    st.success(f"Criminal saved: {saved}")
                    st.session_state["last_causelist_saved"] = str(saved)
                    st.session_state["last_causelist_type"] = "criminal"
                else:
                    st.error(
                        "Criminal download failed — possibly invalid captcha. Re-run Prepare to refresh captchas."
                    )
            except Exception as e:
                st.exception(e)
                st.error("Error during Criminal submit. See trace above.")

        # show last saved result (same as before)
        if (
            "last_causelist_saved" in st.session_state
            and st.session_state["last_causelist_saved"]
        ):
            saved_path = Path(st.session_state["last_causelist_saved"])
            if saved_path.exists():
                st.markdown("## Download result")
                st.write("Saved file:", saved_path)
                try:
                    with open(saved_path, "rb") as fh:
                        file_bytes = fh.read()
                    st.download_button(
                        "Download file",
                        data=file_bytes,
                        file_name=saved_path.name,
                        key=f"dl_{saved_path.name}",
                    )
                except Exception:
                    st.write("Download button unavailable (file read error).")

                sfx = saved_path.suffix.lower()
                try:
                    if sfx == ".json":
                        try:
                            st.json(json.loads(saved_path.read_text(encoding="utf-8")))
                        except Exception:
                            st.code(saved_path.read_text(encoding="utf-8"))
                    elif sfx in [".html", ".htm"]:
                        render_html_file(saved_path)
                    elif sfx == ".pdf":
                        embed_pdf_bytes(saved_path.read_bytes())
                    elif sfx in [".txt", ".log", ".js", ".csv"]:
                        st.code(saved_path.read_text(encoding="utf-8"), language="text")
                    else:
                        st.write("Preview not available for this file type.")
                except Exception as e:
                    st.exception(e)
                    st.write("Could not render preview; download to inspect the file.")
            else:
                st.warning(
                    "Previously saved file cannot be found on disk. It may have been moved."
                )


# always show sidebar file browser
file_browser_sidebar("outputs")
