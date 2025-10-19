# Scraper for eCourts India

A small, practical scraper for Indian eCourts that supports:

• Single-case lookup by CNR (interactive captcha + parse), and
• Downloading cause lists (requests-based or Selenium-based flows),
• Saving results (pdf/html/json/txt) under outputs/.

The code is split between functions.py (core library), script.py (CLI), and app.py (Streamlit UI).


## Overall Features

    - CNR lookup: fetches captcha, performs POST search, parses result (case metadata, case history, PDF download if present) and saves JSON + a human-readable text report.

    - Cause-list download (requests mode): fill state/district/complex/court, fetch captcha(s), submit and download causelist PDF or HTML; attempts to auto-parse to JSON -- Currently not supported by the e-Courts website.

    - Cause-list download (Selenium mode): runs a browser, triggers the JS-driven UI, captures per-type captchas (Civil/Criminal) and downloads PDF/HTML; useful where server requires JS.

    - Streamlit UI: friendly web interface exposing the same flows (CNR form, prepare + submit captchas for cause lists, embedded previews, file browser).


## Example Usage via CLI (perfectly working)

    ### For CNR search

    python script.py --cnr MHPU050000272025

    ### For Causelist download
    
    python script.py --causelist --state "Maharashtra" --district 
    "Pune" --court-complex "Pune, Civil and Criminal Court" --court-name "1-Smt N. S. Sayyad-CIVIL JUDGE J.D. AND J.M.F.C. PMC PUNE" --date "15-10-2025" --mode selenium 


## Example Usage via Streamlit UI (bug in downloading causelist)

    - streamlit run app.py

    - Open the URL shown by Streamlit (usually http://localhost:8501). 
    The app provides two tabs:

    --> CNR lookup: Fetch captcha → enter captcha → Submit → parsed JSON + downloadable text report are saved to outputs/cnr.

    --> Download Cause List: Choose selenium mode (recommended), fill state/district/complex/court, click Run Download Script (Prepare), then solve Civil and Criminal captchas shown and Submit to download cause-lists to outputs/causelists. Plese refer "Future scope of improvements" section ahead.
    
    - The app includes an outputs file browser and previewer in the sidebar.

    - The app can be further deployed on a VM or on a Render like service.


## Output layout (important paths)

    - outputs/cnr/ --> Text report and json for CNR search result.
    - outputs/causelists/ --> Json and html for downloaded causelists.


## Troubleshooting & tips

    - Captcha: The service requires solving captchas for many actions. 
    Solve promptly as captchas may expire.

    - Selenium failures / timing: If selects/options don’t populate, increase wait time (the Selenium helpers accept wait_timeout) or run non-headless to watch the browser. Selenium uses webdriver-manager so Chromedriver is auto-installed, but make sure a compatible Chrome is installed.

    - No PDF returned: The code saves HTML fallback and attempts to parse it (parse_cause_list_html) into JSON. Check outputs/causelists for .html and .json files.


## Quick checklist before running

    - Create & activate Python virtualenv.
    - pip install -r requirements.txt
    - For Selenium flows, ensure Chrome is installed.
    - Run either python script.py (CLI) or streamlit run app.py (UI).


## Future scope of improvements (and reported bugs)

    - The code for CNR search using Streamlit UI is working fine, however, the code for Download Causelist is having a bug where the fields do not get populated in the selenium browser. However, using the same feature via CLI command works totally fine.
    
    - Captchas can be auto solved using pytesseract OCR.
    However, this is not recommended due to website's security policies.

    - Beautifying UI for the UX.