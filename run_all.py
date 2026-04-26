"""
run_all.py
Master orchestration script — runs all Gold bronze-layer fetchers sequentially.

Usage:
  python run_all.py                         # full history, all fetchers
  python run_all.py --start 2020-01-01      # custom start date
  python run_all.py --fetchers fred lbma    # run specific fetchers only
  python run_all.py --dry-run               # validate config only, no fetching

Each fetcher is run in a try/except so one failure does not block others.
A summary table is printed at the end showing what succeeded/failed.
"""

import argparse
import sys
import time
from datetime import datetime

from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import get_logger

logger = get_logger("run_all")


FETCHERS = {
    "fred":         ("fetchers.fred_fetcher",        "FRED macro series"),
    "yfinance":     ("fetchers.yfinance_fetcher",     "ETF prices + COMEX futures curve"),
    "lbma":         ("fetchers.lbma_fetcher",         "LBMA gold price fix"),
    "fomc":         ("fetchers.fomc_fetcher",          "FOMC statements + minutes"),
    "fed_speeches": ("fetchers.fed_speeches_fetcher",  "Fed governor speeches"),
    "gdelt":        ("fetchers.gdelt_fetcher",         "GDELT gold headlines"),
    "macro_text":   ("fetchers.macro_text_fetcher",    "BLS CPI/PPI + Treasury + Beige Book"),
    "cftc_cot":     ("fetchers.cftc_cot_fetcher",      "CFTC COT gold positioning"),
    "comex_inv":    ("fetchers.comex_inventory_fetcher","COMEX gold registered/eligible inventory"),
    "etf_flows":    ("fetchers.etf_flows_fetcher",      "GLD/IAU shares outstanding + AUM"),
}


def run_fetcher(module_path: str, start: str, end: str) -> tuple[bool, int, str]:
    """
    Dynamically import and run a fetcher module.
    Returns (success: bool, row_count: int, message: str)
    """
    import importlib
    mod = importlib.import_module(module_path)
    df = mod.run(start=start, end=end)
    n = len(df) if df is not None and not df.empty else 0
    return True, n, f"{n:,} rows"


def main():
    parser = argparse.ArgumentParser(description="I-FWM Gold bronze-layer fetcher")
    parser.add_argument("--start",    default=DEFAULT_START, help="Start date YYYY-MM-DD")
    parser.add_argument("--end",      default=DEFAULT_END,   help="End date YYYY-MM-DD")
    parser.add_argument("--fetchers", nargs="+", choices=list(FETCHERS.keys()),
                        default=list(FETCHERS.keys()), help="Which fetchers to run")
    parser.add_argument("--dry-run",  action="store_true", help="Print plan only")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("I-FWM Gold Bronze Layer — Data Fetch Run")
    logger.info(f"  Date range : {args.start} → {args.end}")
    logger.info(f"  Fetchers   : {', '.join(args.fetchers)}")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("DRY RUN — no data fetched.")
        for key in args.fetchers:
            module, desc = FETCHERS[key]
            logger.info(f"  Would run: {key:15s} ({desc})")
        return

    results = []
    total_start = time.time()

    for key in args.fetchers:
        module, desc = FETCHERS[key]
        logger.info(f"\n{'─'*50}")
        logger.info(f"Running: {key} — {desc}")
        t0 = time.time()
        try:
            success, n_rows, msg = run_fetcher(module, args.start, args.end)
            elapsed = time.time() - t0
            results.append((key, "OK", n_rows, f"{elapsed:.1f}s"))
            logger.info(f"  Done in {elapsed:.1f}s — {msg}")
        except Exception as e:
            elapsed = time.time() - t0
            results.append((key, "FAILED", 0, str(e)[:80]))
            logger.error(f"  FAILED after {elapsed:.1f}s: {e}")

    # Summary
    total_elapsed = time.time() - total_start
    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"{'Fetcher':<18} {'Status':<8} {'Rows':>10}  {'Detail'}")
    logger.info(f"{'─'*18} {'─'*8} {'─'*10}  {'─'*30}")
    for key, status, n_rows, detail in results:
        row_str = f"{n_rows:,}" if n_rows else "—"
        logger.info(f"{key:<18} {status:<8} {row_str:>10}  {detail}")

    n_ok     = sum(1 for r in results if r[1] == "OK")
    n_failed = sum(1 for r in results if r[1] == "FAILED")
    logger.info(f"\n  {n_ok} succeeded, {n_failed} failed — total time {total_elapsed:.1f}s")

    if n_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()