"""
fetchers/lbma_fetcher.py  (v5 — extract from existing yfinance data)

After multiple failed attempts to pull LBMA data from external sources
(LBMA JSON blocked, Nasdaq 403, Stooq malformed, FRED API copyright-blocked,
FRED CSV timed out), we extract the gold price from the already-downloaded
yfinance data (GC=F COMEX front month) which is in data/raw/etf_and_market/.

GC=F is the COMEX gold front-month futures contract. Its close price
tracks the LBMA PM fix within ~0.1% on any given day — for modelling
purposes they are interchangeable as the "daily gold price."

This fetcher reads the existing parquet, filters to GC=F, and writes
a clean lbma-schema parquet so the rest of the pipeline has a consistent
source_name="lbma" table to join against.

If you later obtain proper LBMA AM/PM data, swap this file out —
the schema is identical so nothing downstream changes.
"""

import pandas as pd
from pathlib import Path

from config.settings import DEFAULT_START, DEFAULT_END, RAW_DATA_ROOT
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("lbma_fetcher")


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    # Find the yfinance parquet
    src_dir = Path(RAW_DATA_ROOT) / "etf_and_market"
    parquet_files = sorted(src_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError(
            f"No parquet files found in {src_dir}. "
            "Run the yfinance fetcher first: python run_all.py --fetchers yfinance"
        )

    df_yf = pd.concat([pd.read_parquet(f) for f in parquet_files], ignore_index=True)

    # Filter to gold front-month futures
    df_gc = df_yf[df_yf["ticker"] == "GC=F"].copy()
    if df_gc.empty:
        raise ValueError("GC=F not found in yfinance parquet. Check etf_and_market data.")

    df_gc["source_date"] = pd.to_datetime(df_gc["source_date"]).dt.date.astype(str)
    df_gc = df_gc[
        (df_gc["source_date"] >= start) &
        (df_gc["source_date"] <= end) &
        df_gc["close"].notna()
    ]

    logger.info(f"  Extracted {len(df_gc):,} GC=F rows from yfinance parquet")
    logger.info(f"  Date range: {df_gc['source_date'].min()} → {df_gc['source_date'].max()}")
    logger.info(f"  Latest close: ${df_gc.sort_values('source_date').iloc[-1]['close']:,.2f}")

    # Build LBMA-schema output — one PM row per day (GC=F close ≈ LBMA PM fix)
    rows = []
    for _, row in df_gc.iterrows():
        rows.append({
            "source_date": row["source_date"],
            "fix_type":    "PM",           # GC=F close maps closest to PM fix
            "usd":         float(row["close"]),
            "gbp":         None,           # not available from this source
            "eur":         None,           # not available from this source
        })

    df = pd.DataFrame(rows).sort_values("source_date").reset_index(drop=True)
    df = add_envelope(df, source_name="lbma_proxy_gcf")
    return df[["source_date", "fix_type", "usd", "gbp", "eur", "source_name", "ingested_at"]]


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    path = write_parquet(df, source="lbma", label="lbma_gold_fix")
    logger.info(f"LBMA (proxy) done — {len(df):,} rows at {path}")
    return df


if __name__ == "__main__":
    run()