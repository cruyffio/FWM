"""
fetchers/cftc_cot_fetcher.py
Fetches CFTC Commitments of Traders (COT) disaggregated futures-only data
for COMEX gold (contract code 088691).

Source: CFTC historical compressed files
URL pattern: https://www.cftc.gov/files/dea/history/fut_disagg_txt_{YYYY}.zip
Each ZIP contains a CSV with ALL commodities for that year.
We filter to gold only (Market_and_Exchange_Names contains "GOLD").

Data available from 2009 onwards (disaggregated report introduced June 2006,
bulk files available from 2009).

Trader categories (what each means for gold):
  Producer/Merchant  — gold miners, refiners, jewelers (commercial hedgers)
  Swap Dealers       — banks hedging OTC gold swaps (JP Morgan, HSBC etc.)
  Managed Money      — hedge funds, CTAs (speculative — KEY signal)
  Other Reportables  — large non-commercial traders

Key derived signals:
  managed_money_net  = mm_long - mm_short  (primary speculative positioning)
  commercial_net     = (prod_long + swap_long) - (prod_short + swap_short)
  open_interest      = total contracts outstanding

Schema (per row):
  source_date          DATE    — report date (Tuesday of that week)
  open_interest        FLOAT
  prod_merc_long       FLOAT   — Producer/Merchant long
  prod_merc_short      FLOAT
  swap_long            FLOAT   — Swap Dealer long
  swap_short           FLOAT
  swap_spreading       FLOAT
  managed_money_long   FLOAT   — Managed Money (hedge funds) long
  managed_money_short  FLOAT
  managed_money_spread FLOAT
  other_long           FLOAT   — Other Reportables long
  other_short          FLOAT
  managed_money_net    FLOAT   — derived: mm_long - mm_short
  commercial_net       FLOAT   — derived: (prod+swap long) - (prod+swap short)
  source_name          TEXT
  ingested_at          TEXT
"""

import io
import time
import zipfile
import requests
import pandas as pd
from datetime import datetime

from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("cftc_cot_fetcher")

CFTC_ZIP_URL  = "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip"
GOLD_CODE     = "088691"   # COMEX gold contract code
HEADERS       = {"User-Agent": "Mozilla/5.0 (research project)"}
REQUEST_DELAY = 2.0

# Column name mapping from CFTC CSV to our schema
# The CFTC uses very long column names — we map to shorter ones
COL_MAP = {
    "Open_Interest_All":                          "open_interest",
    "Prod_Merc_Positions_Long_All":               "prod_merc_long",
    "Prod_Merc_Positions_Short_All":              "prod_merc_short",
    "Swap_Positions_Long_All":                    "swap_long",
    "Swap__Positions_Short_All":                  "swap_short",
    "Swap__Positions_Spreading_All":              "swap_spreading",
    "M_Money_Positions_Long_All":                 "managed_money_long",
    "M_Money_Positions_Short_All":                "managed_money_short",
    "M_Money_Positions_Spreading_All":            "managed_money_spread",
    "Other_Rept_Positions_Long_All":              "other_long",
    "Other_Rept_Positions_Short_All":             "other_short",
}


def _fetch_year(year: int) -> pd.DataFrame:
    url = CFTC_ZIP_URL.format(year=year)
    logger.info(f"  Downloading CFTC COT {year}: {url}")
    time.sleep(REQUEST_DELAY)

    try:
        r = requests.get(url, headers=HEADERS, timeout=60)
        r.raise_for_status()
    except Exception as e:
        logger.error(f"  Failed {year}: {e}")
        return pd.DataFrame()

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
        csv_name = z.namelist()[0]
        with z.open(csv_name) as f:
            df = pd.read_csv(f, low_memory=False)
    except Exception as e:
        logger.error(f"  Parse failed {year}: {e}")
        return pd.DataFrame()

    logger.info(f"  {year}: {len(df):,} total rows, {len(df.columns)} columns")

    # Filter to COMEX gold
    if "CFTC_Contract_Market_Code" in df.columns:
        df = df[df["CFTC_Contract_Market_Code"].astype(str).str.strip() == GOLD_CODE]
    elif "Market_and_Exchange_Names" in df.columns:
        df = df[df["Market_and_Exchange_Names"].str.upper().str.contains("GOLD")]

    if df.empty:
        logger.warning(f"  {year}: no gold rows found")
        return pd.DataFrame()

    logger.info(f"  {year}: {len(df)} gold COT rows")
    return df


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    start_year = max(int(start[:4]), 2009)   # disaggregated available from 2009
    end_year   = int(end[:4])

    all_frames = []
    for year in range(start_year, end_year + 1):
        df = _fetch_year(year)
        if not df.empty:
            all_frames.append(df)

    if not all_frames:
        logger.warning("No COT data fetched.")
        return pd.DataFrame()

    raw = pd.concat(all_frames, ignore_index=True)

    # Parse date — CFTC uses "As_of_Date_In_Form_YYMMDD" or "Report_Date_as_YYYY-MM-DD"
    if "Report_Date_as_YYYY-MM-DD" in raw.columns:
        raw["source_date"] = pd.to_datetime(raw["Report_Date_as_YYYY-MM-DD"]).dt.date.astype(str)
    elif "As_of_Date_In_Form_YYMMDD" in raw.columns:
        raw["source_date"] = pd.to_datetime(
            raw["As_of_Date_In_Form_YYMMDD"].astype(str), format="%y%m%d"
        ).dt.date.astype(str)
    else:
        logger.error("Cannot find date column in COT data")
        return pd.DataFrame()

    # Filter date range
    raw = raw[(raw["source_date"] >= start) & (raw["source_date"] <= end)]

    # Rename columns we care about
    raw = raw.rename(columns=COL_MAP)

    # Keep only mapped columns + source_date
    keep = ["source_date"] + [c for c in COL_MAP.values() if c in raw.columns]
    df = raw[keep].copy()

    # Convert to numeric
    for col in keep[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Derive key signals
    if "managed_money_long" in df.columns and "managed_money_short" in df.columns:
        df["managed_money_net"] = df["managed_money_long"] - df["managed_money_short"]

    if all(c in df.columns for c in ["prod_merc_long","prod_merc_short","swap_long","swap_short"]):
        df["commercial_net"] = (
            (df["prod_merc_long"] + df["swap_long"]) -
            (df["prod_merc_short"] + df["swap_short"])
        )

    df = df.sort_values("source_date").reset_index(drop=True)
    df = add_envelope(df, source_name="cftc_cot")
    logger.info(f"  Total COT rows: {len(df):,}  ({df['source_date'].min()} → {df['source_date'].max()})")
    return df


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        return df
    path = write_parquet(df, source="cftc_cot", label="cot_gold_disaggregated")
    logger.info(f"CFTC COT done — {len(df):,} rows at {path}")
    return df


if __name__ == "__main__":
    run()