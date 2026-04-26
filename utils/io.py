"""
utils/io.py
Shared helpers: directory setup, logging, standardised Parquet writer.
Every fetcher imports from here to guarantee envelope-column consistency.
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from config.settings import RAW_DATA_ROOT, LOG_DIR, LOG_LEVEL


# ── Logging ──────────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)
    log_file = Path(LOG_DIR) / f"{name}.log"

    logger = logging.getLogger(name)
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, LOG_LEVEL))
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")

    fh = logging.FileHandler(log_file)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


# ── Secrets loader ───────────────────────────────────────────────────────────

def load_secrets(path: str = "config/secrets.yaml") -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Missing {path}. Copy config/secrets.example.yaml → {path} and fill in keys."
        )
    with open(path) as f:
        return yaml.safe_load(f)


# ── Directory helpers ────────────────────────────────────────────────────────

def raw_dir(source: str) -> Path:
    """Return (and create) the raw data directory for a given source."""
    p = Path(RAW_DATA_ROOT) / source
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Envelope columns ─────────────────────────────────────────────────────────

def add_envelope(df: pd.DataFrame, source_name: str) -> pd.DataFrame:
    """
    Add the three mandatory bronze-layer envelope columns to any DataFrame.

    Columns added:
      - source_name  : string identifier of the data feed
      - ingested_at  : UTC timestamp of when THIS pipeline run pulled the data
      - (source_date must already exist in df as a date/datetime column)
    """
    if "source_date" not in df.columns:
        raise ValueError("DataFrame must contain a 'source_date' column before calling add_envelope().")

    df = df.copy()
    df["source_name"] = source_name
    df["ingested_at"] = datetime.now(timezone.utc).isoformat()

    # Normalise source_date to date type
    df["source_date"] = pd.to_datetime(df["source_date"]).dt.date

    return df


# ── Parquet writer ───────────────────────────────────────────────────────────

def write_parquet(df: pd.DataFrame, source: str, label: str) -> Path:
    """
    Write df to  data/raw/<source>/<YYYY-MM-DD>_<label>.parquet
    Returns the path written.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_dir = raw_dir(source)
    out_path = out_dir / f"{today}_{label}.parquet"

    df.to_parquet(out_path, index=False, engine="pyarrow")
    logger = get_logger("io")
    logger.info(f"Wrote {len(df):,} rows → {out_path}")
    return out_path


# ── Date helpers ─────────────────────────────────────────────────────────────

def date_range_str(start: str, end: str) -> str:
    return f"{start} → {end}"
