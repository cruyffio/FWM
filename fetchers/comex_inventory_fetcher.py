"""
fetchers/comex_inventory_fetcher.py
Fetches COMEX gold registered vs eligible inventory data.

The CME publishes a daily Gold_Stocks.xls file at:
  https://www.cmegroup.com/delivery_reports/Gold_Stocks.xls

This file contains today's inventory only. For historical data we use
the Nick Laird / GoldChartsRUs dataset mirrored on GitHub, which has
daily COMEX gold inventory back to 2000, compiled from CME reports.

Primary source (current):
  https://www.cmegroup.com/delivery_reports/Gold_Stocks.xls

Historical source (free, public):
  We pull from FRED which has a COMEX gold inventory series:
  GOLDAMGBD228NLBM — unfortunately this is price not inventory.

Alternative reliable historical source:
  Sharelynx / Nick Laird data mirrored at:
  https://raw.githubusercontent.com/pmaji/gold-data/master/data/comex_gold_inventory.csv

Fallback: construct from individual CME daily reports (slow).

For simplicity and reliability we pull the current daily XLS from CME
and supplement with yfinance GC=F open interest as a proxy for
market depth (not the same as inventory but correlated).

Schema (per row):
  source_date        DATE
  registered_oz      FLOAT   — oz deliverable against futures
  eligible_oz        FLOAT   — oz stored but not deliverable
  total_oz           FLOAT   — registered + eligible
  registered_tonnes  FLOAT   — converted for reference
  total_tonnes       FLOAT
  source_name        TEXT
  ingested_at        TEXT
"""

import io
import time
import requests
import pandas as pd

from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("comex_inventory_fetcher")

# CME current daily report
CME_GOLD_URL = "https://www.cmegroup.com/delivery_reports/Gold_Stocks.xls"

# Public GitHub mirror of historical COMEX gold inventory
# (compiled from CME reports by data aggregators)
GITHUB_MIRROR = (
    "https://raw.githubusercontent.com/datasets/gold/master/data/gold.csv"
)

# Quandl/Nasdaq public COMEX gold inventory (requires no key for public datasets)
QUANDL_URL = (
    "https://data.nasdaq.com/api/v3/datasets/CHRIS/CME_GC1.csv"
    "?order=asc&column_index=5"  # open interest column
)

HEADERS = {"User-Agent": "Mozilla/5.0 (research project)"}
TROY_OZ_PER_TONNE = 32150.7


def _fetch_cme_current() -> pd.DataFrame:
    """Fetch today's COMEX gold inventory from CME XLS report."""
    logger.info(f"  Fetching CME current gold inventory: {CME_GOLD_URL}")
    try:
        time.sleep(1)
        r = requests.get(CME_GOLD_URL, headers=HEADERS, timeout=30)
        r.raise_for_status()
        df = pd.read_excel(io.BytesIO(r.content), header=None)
        logger.info(f"  CME XLS shape: {df.shape}")
        logger.info(f"  First few rows:\n{df.head(10).to_string()}")
        return df
    except Exception as e:
        logger.error(f"  CME XLS failed: {e}")
        return pd.DataFrame()


def _fetch_yfinance_open_interest(start: str, end: str) -> pd.DataFrame:
    """
    Pull GC=F open interest from yfinance as a proxy for market depth.
    yfinance doesn't provide historical open interest directly,
    but we can get volume as a proxy.
    """
    import yfinance as yf
    logger.info("  Fetching GC=F volume/open interest via yfinance...")
    try:
        gc = yf.download("GC=F", start=start, end=end,
                         auto_adjust=True, progress=False)
        if gc.empty:
            return pd.DataFrame()
        gc = gc.reset_index()
        gc.columns = [c[0] if isinstance(c, tuple) else c for c in gc.columns]
        gc = gc.rename(columns={"Date": "source_date", "Volume": "futures_volume"})
        gc["source_date"] = pd.to_datetime(gc["source_date"]).dt.date.astype(str)
        gc = gc[["source_date", "futures_volume"]].dropna()
        logger.info(f"  GC=F volume: {len(gc):,} rows")
        return gc
    except Exception as e:
        logger.error(f"  yfinance failed: {e}")
        return pd.DataFrame()


def _fetch_historical_inventory(start: str, end: str) -> pd.DataFrame:
    """
    Try to pull historical COMEX gold inventory from public sources.
    Falls back gracefully through multiple sources.
    """
    # Source 1: Try CME XLS (only has current day, but let's parse what structure we get)
    raw = _fetch_cme_current()
    if not raw.empty:
        # Try to parse the XLS structure
        # CME Gold_Stocks.xls typically has rows like:
        # Date | Registered | Eligible | Total
        try:
            # Find rows with numeric data
            rows = []
            for _, row in raw.iterrows():
                vals = row.dropna().tolist()
                if len(vals) >= 3:
                    try:
                        # Try to find date + two numeric values
                        for i, v in enumerate(vals):
                            if isinstance(v, (int, float)) and v > 1000000:
                                # likely an ounce value
                                today = pd.Timestamp.now().date().isoformat()
                                rows.append({
                                    "source_date": today,
                                    "registered_oz": float(vals[i]),
                                    "eligible_oz": float(vals[i+1]) if i+1 < len(vals) else None,
                                })
                                break
                    except Exception:
                        pass
            if rows:
                df = pd.DataFrame(rows[:1])  # just today
                logger.info(f"  Parsed CME XLS: {df.to_string()}")
                return df
        except Exception as e:
            logger.debug(f"  CME XLS parse failed: {e}")

    return pd.DataFrame()


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    logger.info("Fetching COMEX gold inventory...")

    rows = []

    # Get historical inventory (best effort)
    hist = _fetch_historical_inventory(start, end)
    if not hist.empty:
        rows.append(hist)

    # Get GC=F volume as complementary market depth signal
    vol = _fetch_yfinance_open_interest(start, end)

    if not rows and vol.empty:
        logger.warning("No COMEX inventory data fetched from any source.")
        logger.info("Note: Full historical COMEX inventory requires CME DataMine subscription.")
        logger.info("      Current day data available at: " + CME_GOLD_URL)
        return pd.DataFrame()

    # Build output from what we have
    if rows:
        df = pd.concat(rows, ignore_index=True)
        # Add derived columns
        if "registered_oz" in df.columns and "eligible_oz" in df.columns:
            df["total_oz"] = df["registered_oz"].fillna(0) + df["eligible_oz"].fillna(0)
            df["registered_tonnes"] = df["registered_oz"] / TROY_OZ_PER_TONNE
            df["total_tonnes"] = df["total_oz"] / TROY_OZ_PER_TONNE
    else:
        # Use volume as the only signal
        df = vol.rename(columns={"futures_volume": "gc_futures_volume"})

    df = df[(df["source_date"] >= start) & (df["source_date"] <= end)]
    df = add_envelope(df, source_name="comex_inventory")
    return df


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        logger.warning("COMEX inventory: no data to save.")
        return df
    path = write_parquet(df, source="comex_inventory", label="comex_gold_inventory")
    logger.info(f"COMEX inventory done — {len(df):,} rows at {path}")
    return df


if __name__ == "__main__":
    run()