# app.py
"""
A Streamlit front-end app that uses scraper.py
"""
import streamlit as st
from functions import get_case_listing
from utils import save_json
import json

st.set_page_config(page_title="eCourts Scraper Demo", layout="wide")
st.title("eCourts Scraper — Demo")

with st.sidebar:
    st.header("Input")
    cnr = st.text_input("CNR (preferred)", help="Enter CNR like MAHHC...")
    case_type = st.text_input("Case Type (if no CNR, optional)")
    case_number = st.text_input("Case Number")
    year = st.text_input("Year")
    check_btn = st.button("Check Case")
    save_output = st.checkbox("Save output to JSON")

if check_btn:
    if not cnr and not (case_type and case_number and year):
        st.error("Please provide CNR or full case (type, number, year).")
    else:
        with st.spinner("Fetching..."):
            res = get_case_listing(
                cnr=cnr or None,
                case_type=(case_type or None),
                case_number=(int(case_number) if case_number.isdigit() else None),
                year=(int(year) if year.isdigit() else None),
            )
        if res.get("error"):
            st.error(res.get("message", "An error occurred"))
            st.code(res.get("raw_html", "")[:2000])
        else:
            st.success("Fetched result")
            st.write("**Listed:**", res.get("listed"))
            st.write("**Court:**", res.get("court_name"))
            st.write("**Serial No:**", res.get("serial_no"))
            if res.get("pdf_url"):
                st.markdown(f"[Download PDF]({res['pdf_url']})")
            if st.checkbox("Show raw JSON"):
                st.json(res)
            if save_output:
                path = f"outputs/{cnr or (case_type + '-' + case_number)}.json"
                save_json(res, path)
                st.info(f"Saved to {path}")
