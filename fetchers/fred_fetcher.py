"""
fetchers/fred_fetcher.py
Pulls all configured FRED series and writes a single bronze Parquet.

Schema (per row):
  source_date   DATE     — observation date from FRED
  series_id     TEXT     — FRED series identifier (e.g. DFII10)
  label         TEXT     — human-readable name from settings
  value         FLOAT    — observation value
  unit          TEXT     — unit string (pct / index / etc.)
  source_name   TEXT     — "fred"
  ingested_at   TEXT     — UTC ingest timestamp
"""

from config.settings import FRED_SERIES, DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, load_secrets, write_parquet

import pandas as pd

logger = get_logger("fred_fetcher")


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    try:
        from fredapi import Fred
    except ImportError:
        raise ImportError("Run: pip install fredapi")

    secrets = load_secrets()
    fred = Fred(api_key=secrets["fred"]["api_key"])

    frames = []
    for series_id, label, unit in FRED_SERIES:
        logger.info(f"Pulling FRED {series_id} ({label}) ...")
        try:
            s = fred.get_series(series_id, observation_start=start, observation_end=end)
            df = s.reset_index()
            df.columns = ["source_date", "value"]
            df["series_id"] = series_id
            df["label"] = label
            df["unit"] = unit
            df = df.dropna(subset=["value"])
            frames.append(df)
            logger.info(f"  {series_id}: {len(df):,} observations")
        except Exception as e:
            logger.error(f"  FAILED {series_id}: {e}")

    if not frames:
        raise RuntimeError("No FRED series fetched successfully.")

    combined = pd.concat(frames, ignore_index=True)
    combined = add_envelope(combined, source_name="fred")

    # Column order
    combined = combined[["source_date", "series_id", "label", "value", "unit", "source_name", "ingested_at"]]
    return combined


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    path = write_parquet(df, source="fred", label="fred_series")
    logger.info(f"FRED done — {len(df):,} rows at {path}")
    return df


if __name__ == "__main__":
    run()
