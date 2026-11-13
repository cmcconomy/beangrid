import argparse
import json
import logging
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from beangrid.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="beangrid", description="Canadian coffee bean price survey")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run", help="scrape all sites, update the archive and build the site")
    run_p.add_argument("--date", type=date.fromisoformat, help="override run date (UTC)")
    one = sub.add_parser("probe", help="scrape one site and print how its products were classified")
    one.add_argument("url")
    one.add_argument("--all", action="store_true", help="include non-coffee products")
    bf = sub.add_parser("backfill", help="rebuild data/history/ from git history of docs/beangrid.csv")
    bf.add_argument("ref", nargs="?", default="HEAD")
    cv = sub.add_parser("canvass", help="comment out dead stores and look for new Canadian roasters")
    cv.add_argument("--no-prune", action="store_true", help="skip re-checking the listed stores")
    cv.add_argument("--no-discover", action="store_true", help="skip looking for new stores")
    cv.add_argument("--write", action="store_true", help="rewrite sites.txt (default: report only)")
    cv.add_argument("--report", type=Path, help="also write the markdown report here")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings()

    if args.cmd == "run":
        from beangrid.pipeline import QualityGateError, run

        try:
            summary = run(settings, today=args.date)
        except QualityGateError as e:
            logging.getLogger("beangrid").error("Quality gate failed: %s", e)
            sys.exit(2)
        print(json.dumps(summary))
    elif args.cmd == "probe":
        import asyncio

        from beangrid.fetch import fetch_all
        from beangrid.normalize import normalize_site

        [result] = asyncio.run(fetch_all([args.url.rstrip("/")], settings))
        if not result.ok:
            sys.exit(f"failed: {result.error}")
        for v in normalize_site(result.site_url, result.products):
            if args.all or v.category == "coffee":
                print(
                    f"{v.category:12} {v.title[:45]:45} | {v.variant_title[:28]:28} | ${v.price:>7.2f} | "
                    f"{(v.grams or 0):>6.0f}g ({v.weight_source[:8]}) | "
                    f"{'$' + format(v.price_per_kg, '.0f') + '/kg' if v.price_per_kg else '':>9} "
                    f"{','.join(v.flags)} [{v.category_reason}]"
                )
    elif args.cmd == "canvass":
        import asyncio

        from beangrid.canvass import canvass
        from beangrid.pipeline import load_site_status

        today = datetime.now(UTC).date()
        status = load_site_status(settings.data_dir / "sites.json")
        report, new_sites = asyncio.run(
            canvass(settings, today, status, prune=not args.no_prune, discover=not args.no_discover)
        )
        text = report.markdown(today)
        print(text)
        if args.report:
            args.report.write_text(text + "\n")
        if args.write and report.changed:
            settings.sites_file.write_text(new_sites)
            logging.getLogger("beangrid").info("Updated %s", settings.sites_file)
    elif args.cmd == "backfill":
        from beangrid.backfill import backfill

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        h = backfill(settings, args.ref)
        logging.getLogger("beangrid").info("Backfill: %d variants since %s", len(h.variants), h.start)


if __name__ == "__main__":
    main()
