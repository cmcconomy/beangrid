import argparse
import json
import logging
import sys
from datetime import date

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
    elif args.cmd == "backfill":
        from beangrid.backfill import backfill

        settings.data_dir.mkdir(parents=True, exist_ok=True)
        h = backfill(settings, args.ref)
        logging.getLogger("beangrid").info("Backfill: %d variants since %s", len(h.variants), h.start)


if __name__ == "__main__":
    main()
