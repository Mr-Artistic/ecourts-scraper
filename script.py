# region ---------- Chapter 1: Imports ----------

import argparse
from pathlib import Path
import requests

from functions import (
    get_case_listing,
    print_case_summary,
    print_case_history,
    generate_text_report,
    download_entire_cause_list,
    download_entire_cause_list_selenium,
    _get_app_token_and_captcha,
)

# endregion Imports


# region ---------- Chapter 2: Main Function for CLI ----------


def main():
    """Main function to parse arguments in the cli."""
    parser = argparse.ArgumentParser(description="eCourts scraper")

    # Argument: Causelist download
    parser.add_argument(
        "--causelist", action="store_true", help="Download entire cause list for today"
    )

    # Argument: State, District, Court Complex, Court Name (for Causelist)
    parser.add_argument("--state", help="State name or code (e.g. Maharashtra or 1)")
    parser.add_argument("--district", help="District name or code (e.g. Pune or 25)")

    parser.add_argument("--court-complex", help="Court complex visible name (optional)")
    parser.add_argument("--court-name", help="Court visible name (optional)")

    # Argument: Optional Causelist Output Directory (Default=outputs/causelists)
    parser.add_argument(
        "--outdir", default="outputs/causelists", help="Output directory"
    )

    # Argument: Optional Causelist Date (Default=Today)
    parser.add_argument(
        "--date",
        "--causelist-date",
        dest="causelist_date",
        help="Optional causelist date (e.g. '18-10-2025' or '2025-10-18'). If omitted, uses today.",
    )

    # Argument: Causelist Fetch Mode (Requests [default] or Selenium)
    parser.add_argument(
        "--mode",
        choices=("requests", "selenium"),
        default="requests",
        help="Mode to fetch cause lists",
    )

    # Argument: CNR Search
    parser.add_argument("--cnr", help="CNR number to search (e.g. MHPU050000272025)")

    # Argument Variable
    args = parser.parse_args()

    # Require that user passes either --cnr (CNR search) OR --causelist (download cause lists)
    if not args.cnr and not args.causelist:
        parser.error(
            "Please provide --cnr for individual case lookup OR --causelist to download cause lists for today."
        )

    # If causelist flow requested, run it and exit

    if args.causelist:
        if args.mode == "selenium":
            if not args.state or not args.district:
                parser.error(
                    "For --mode selenium please pass both --state and --district."
                )

            download_entire_cause_list_selenium(
                state=args.state,
                district=args.district,
                court_complex=args.court_complex,
                court_name=args.court_name,
                out_dir=args.outdir,
                headless=False,
                wait_timeout=20,
                causelist_date=args.causelist_date,
            )

            return

        else:

            # Require court-complex and court-name for requests mode
            if not args.state or not args.district:
                parser.error(
                    "For --causelist please pass both --state and --district (name or code)."
                )

            if not args.court_complex or not args.court_name:
                parser.error(
                    "For --mode requests please pass both --court-complex and --court-name."
                )

            sess = requests.Session()
            try:
                # Initialize session & download captcha image (keeps existing behavior)
                _get_app_token_and_captcha(sess)
            except Exception:
                pass

            idx = download_entire_cause_list(
                sess,
                state=args.state,
                district=args.district,
                court_complex=args.court_complex,
                court_name=args.court_name,
                out_dir=args.outdir,
                interactive=True,
                causelist_date=args.causelist_date,
            )

            print("Cause-list index saved at:", idx)
            return

    # Otherwise handle CNR lookup
    if args.cnr:
        data = get_case_listing(args.cnr)
        # Print summary + history on console
        print_case_summary(data)
        print_case_history(data)
        # Build report path
        txt_path = Path("outputs/cnr") / f"{args.cnr}_report.txt"
        # Generate text report
        generate_text_report(data, str(txt_path))
        print(f"\n✅ Report generated at: {txt_path}\n")


# endregion Main Function for CLI

if __name__ == "__main__":
    main()
