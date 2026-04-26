"""
fetchers/gdelt_fetcher.py  (v3 — GKG CSV approach, no rate limits)

Switches from the rate-limited DOC API to GDELT's raw GKG CSV files,
served from a plain HTTP file server with no API key and no rate limiting.

GKG daily files: http://data.gdeltproject.org/gkg/YYYYMMDD.gkg.csv.zip
One file per day, ~5-20MB zipped. We download, filter for gold-relevant
themes, and extract article URLs + tone scores.

Schema (per row):
  source_date   DATE
  url           TEXT
  domain        TEXT
  tone          FLOAT  — GKG overall tone (negative = bearish/fearful)
  themes        TEXT   — matched GDELT theme codes
  source_name   TEXT
  ingested_at   TEXT
"""

import io
import json
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import requests
import pandas as pd

from config.settings import GDELT_MAX_ARTICLES_PER_DAY, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("gdelt_fetcher")

GDELT_DEFAULT_START = "2015-01-01"
GKG_BASE_URL        = "http://data.gdeltproject.org/gkg/{date}.gkg.csv.zip"
REQUEST_DELAY       = 1.0   # 1 second between downloads — no throttling needed
CHECKPOINT_FILE     = "data/raw/gdelt/.gdelt_checkpoint.json"

# GDELT GKG theme codes relevant to gold and macro drivers
GOLD_THEMES = [
    "ECON_GOLD", "FNCACT_GOLD", "ECON_PRECIOUS_METALS",
    "ECON_CENTRALBANK", "ECON_MONETARYPOLICY", "ECON_INFLATION",
    "ECON_COMMODITIES", "FNCACT_MINER", "EPU_POLICY_UNCERTAINTY",
    "ECON_INTEREST_RATES", "ECON_CURRENCY",
]

GKG_COLS = [
    "date", "numarts", "counts", "themes", "locations",
    "persons", "organizations", "tone", "cameoeventids",
    "sources", "sourceurls",
]


# ── Checkpoint ────────────────────────────────────────────────────────────────

def _load_checkpoint() -> dict:
    p = Path(CHECKPOINT_FILE)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"completed_dates": [], "rows": []}

def _save_checkpoint(state: dict):
    p = Path(CHECKPOINT_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state))

def _clear_checkpoint():
    p = Path(CHECKPOINT_FILE)
    if p.exists():
        p.unlink()


# ── Per-day fetch ─────────────────────────────────────────────────────────────

def _fetch_day(date_str: str) -> list[dict]:
    """Download and parse one day's GKG file. date_str = 'YYYYMMDD'."""
    url = GKG_BASE_URL.format(date=date_str)
    try:
        time.sleep(REQUEST_DELAY)
        r = requests.get(url, timeout=60)
        if r.status_code == 404:
            logger.debug(f"  No GKG file for {date_str} (weekend/holiday)")
            return []
        r.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.error(f"  Download failed {date_str}: {e}")
        return []

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        with z.open(z.namelist()[0]) as f:
            df = pd.read_csv(
                f, sep="\t", header=None, names=GKG_COLS,
                dtype=str, on_bad_lines="skip",
            )
    except Exception as e:
        logger.error(f"  Parse failed {date_str}: {e}")
        return []

    if df.empty:
        return []

    # Filter: keep rows whose themes contain a gold-relevant code
    themes_col = df["themes"].fillna("").str.upper()
    mask = themes_col.apply(lambda t: any(g in t for g in GOLD_THEMES))
    df_gold = df[mask].copy()

    if df_gold.empty:
        return []

    def parse_tone(t):
        try:
            return float(str(t).split(",")[0])
        except Exception:
            return None

    df_gold["tone_val"] = df_gold["tone"].apply(parse_tone)

    iso_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    rows = []
    seen = set()

    for _, row in df_gold.iterrows():
        raw_urls = str(row.get("sourceurls", "")).split("<UDIV>")
        for u in raw_urls:
            u = u.strip()
            if not u or u in seen:
                continue
            seen.add(u)
            domain = u.split("/")[2] if u.startswith("http") else ""
            rows.append({
                "source_date": iso_date,
                "url":         u,
                "domain":      domain,
                "tone":        row["tone_val"],
                "themes":      row.get("themes", "")[:300],
            })

    return rows


# ── Date helpers ──────────────────────────────────────────────────────────────

def _date_range(start: str, end: str):
    d = datetime.strptime(start, "%Y-%m-%d")
    end_d = datetime.strptime(end, "%Y-%m-%d")
    while d <= end_d:
        yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


# ── Main fetch ────────────────────────────────────────────────────────────────

def fetch(
    start:  str  = GDELT_DEFAULT_START,
    end:    str  = DEFAULT_END,
    resume: bool = True,
) -> pd.DataFrame:

    all_dates = list(_date_range(start, end))
    logger.info(f"GDELT GKG fetch: {start} → {end} ({len(all_dates)} days)")
    logger.info("  Method: direct CSV download — no API, no rate limits")

    state     = _load_checkpoint() if resume else {"completed_dates": [], "rows": []}
    completed = set(state["completed_dates"])
    all_rows  = state["rows"]
    seen_urls = {r["url"] for r in all_rows}

    todo = [d for d in all_dates if d not in completed]
    skipped = len(all_dates) - len(todo)
    if skipped:
        logger.info(f"  Resuming — skipping {skipped} already-completed days")
    logger.info(f"  Days to fetch: {len(todo)}")

    try:
        for i, date_str in enumerate(todo):
            logger.info(f"  [{i+1}/{len(todo)}] {date_str} ...")
            rows = _fetch_day(date_str)
            n_new = 0
            for row in rows:
                if row["url"] in seen_urls:
                    continue
                seen_urls.add(row["url"])
                all_rows.append(row)
                n_new += 1
            logger.info(f"    → {n_new} new articles (total: {len(all_rows):,})")
            completed.add(date_str)
            _save_checkpoint({"completed_dates": list(completed), "rows": all_rows})

    except KeyboardInterrupt:
        logger.warning("Interrupted — progress saved. Re-run to resume.")

    if not all_rows:
        logger.warning("No GDELT articles fetched.")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df[(df["source_date"] >= start) & (df["source_date"] <= end)]

    df["tone"] = pd.to_numeric(df["tone"], errors="coerce")
    df = df.sort_values(["source_date", "tone"])
    df = (
        df.groupby("source_date", group_keys=False)
          .apply(lambda g: g.head(GDELT_MAX_ARTICLES_PER_DAY))
          .reset_index(drop=True)
    )

    df = add_envelope(df, source_name="gdelt")
    col_order = ["source_date", "url", "domain", "tone", "themes", "source_name", "ingested_at"]
    return df[[c for c in col_order if c in df.columns]]


def run(start: str = GDELT_DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        logger.warning("GDELT fetcher returned empty DataFrame.")
        return df
    path = write_parquet(df, source="gdelt", label="gdelt_gold_gkg")
    _clear_checkpoint()
    logger.info(f"GDELT done — {len(df):,} articles at {path}")
    return df


if __name__ == "__main__":
    run()