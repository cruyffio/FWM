"""
fetchers/etf_flows_fetcher.py
Fetches GLD and IAU shares outstanding (= AUM proxy = physical gold flow signal).

Shares outstanding × price = AUM = USD value of physical gold held in vault.
When shares outstanding rises, the ETF custodian must buy physical gold.
When it falls, they sell. This is a direct institutional flow signal.

yfinance provides shares outstanding via the Ticker.info dict,
but only as a current snapshot, not historical.

For historical shares outstanding we use:
  - GLD: iShares / State Street publish monthly data
  - Alternative: compute from NAV = price × shares / gold_price × 10
    (GLD holds 1/10th oz per share, IAU holds 1/100th oz per share)

Most reliable free approach: pull daily from yfinance info and
supplement historical with the ETF NAV relationship:
  shares_outstanding_approx = (close_price / gold_price) × conversion × known_baseline

For a research model, we use GLD AUM (in millions of USD) as the signal,
derived from: aum = shares_outstanding × close_price
We already have the close price in etf_and_market.
We pull shares outstanding from yfinance for current value and use
it to calibrate a back-calculation.

Schema (per row):
  source_date            DATE
  ticker                 TEXT    — GLD or IAU
  shares_outstanding     FLOAT   — shares (in millions)
  close_price            FLOAT   — USD per share
  aum_usd_millions       FLOAT   — shares × price / 1e6
  gold_oz_held_millions  FLOAT   — AUM / gold_price × 10 (for GLD)
  source_name            TEXT
  ingested_at            TEXT
"""

import pandas as pd
import yfinance as yf
from pathlib import Path

from config.settings import DEFAULT_START, DEFAULT_END, RAW_DATA_ROOT
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("etf_flows_fetcher")

ETF_CONFIG = {
    "GLD": {"oz_per_share": 0.09264,  "label": "SPDR Gold Shares"},
    "IAU": {"oz_per_share": 0.01,     "label": "iShares Gold Trust"},
}


def _get_shares_outstanding(ticker: str) -> float | None:
    """Get current shares outstanding from yfinance."""
    try:
        info = yf.Ticker(ticker).info
        shares = info.get("sharesOutstanding") or info.get("impliedSharesOutstanding")
        if shares:
            return float(shares)
    except Exception as e:
        logger.debug(f"  Could not get shares outstanding for {ticker}: {e}")
    return None


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    logger.info("Fetching ETF shares outstanding / AUM flows...")

    # Load existing ETF price data from etf_and_market parquet
    src_dir = Path(RAW_DATA_ROOT) / "etf_and_market"
    parquet_files = sorted(src_dir.glob("*.parquet"))
    if not parquet_files:
        raise FileNotFoundError("etf_and_market parquet not found — run yfinance fetcher first")

    df_prices = pd.concat([pd.read_parquet(f) for f in parquet_files], ignore_index=True)
    df_prices["source_date"] = pd.to_datetime(df_prices["source_date"]).dt.date.astype(str)

    # Load gold price for oz calculation
    gold = df_prices[df_prices["ticker"] == "GC=F"][["source_date", "close"]].copy()
    gold = gold.rename(columns={"close": "gold_price"})

    rows = []
    for ticker, cfg in ETF_CONFIG.items():
        df_etf = df_prices[df_prices["ticker"] == ticker][["source_date", "close"]].copy()
        if df_etf.empty:
            logger.warning(f"  No price data for {ticker}")
            continue

        # Get current shares outstanding for calibration
        current_shares = _get_shares_outstanding(ticker)
        if current_shares:
            logger.info(f"  {ticker} current shares outstanding: {current_shares/1e6:.1f}M")
        else:
            # Fallback known approximate values
            current_shares = {"GLD": 385_000_000, "IAU": 2_300_000_000}.get(ticker, 500_000_000)
            logger.warning(f"  {ticker}: using fallback shares outstanding estimate")

        # Join with gold price to derive oz held
        df_merged = df_etf.merge(gold, on="source_date", how="left")
        df_merged["gold_price"] = df_merged["gold_price"].ffill()

        # Derive shares from NAV relationship
        # For GLD: 1 share ≈ 0.0926 oz gold (decreases slightly over time due to fees)
        # AUM = shares × price, oz_held = shares × oz_per_share
        # We use close price / gold_price / oz_per_share to get implied shares,
        # then calibrate to current known shares
        oz_per_share = cfg["oz_per_share"]
        df_merged["implied_ratio"] = df_merged["close"] / df_merged["gold_price"].clip(lower=1)
        # Implied shares from NAV: (ETF price / gold price) / oz_per_share
        df_merged["shares_outstanding"] = (
            df_merged["implied_ratio"] / oz_per_share * current_shares /
            (df_merged["implied_ratio"].iloc[-1] / oz_per_share)
        ).clip(lower=0)

        df_merged["aum_usd_millions"] = (
            df_merged["shares_outstanding"] * df_merged["close"] / 1e6
        ).round(1)

        df_merged["gold_oz_held_millions"] = (
            df_merged["shares_outstanding"] * oz_per_share / 1e6
        ).round(3)

        df_merged["ticker"] = ticker
        df_merged = df_merged.rename(columns={"close": "close_price"})
        df_merged = df_merged[
            (df_merged["source_date"] >= start) &
            (df_merged["source_date"] <= end)
        ]

        keep = ["source_date", "ticker", "shares_outstanding",
                "close_price", "aum_usd_millions", "gold_oz_held_millions"]
        rows.append(df_merged[keep].dropna(subset=["close_price"]))
        logger.info(f"  {ticker}: {len(df_merged):,} rows, "
                    f"latest AUM=${df_merged['aum_usd_millions'].iloc[-1]:.0f}M")

    if not rows:
        logger.warning("No ETF flow data produced.")
        return pd.DataFrame()

    df = pd.concat(rows, ignore_index=True)
    df = df.sort_values(["source_date", "ticker"]).reset_index(drop=True)
    df = add_envelope(df, source_name="etf_flows")
    return df


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        return df
    path = write_parquet(df, source="etf_flows", label="etf_gld_iau_flows")
    logger.info(f"ETF flows done — {len(df):,} rows at {path}")
    return df


if __name__ == "__main__":
    run()