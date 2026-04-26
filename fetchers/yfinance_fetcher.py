"""
fetchers/yfinance_fetcher.py
Pulls price/volume data for ETFs, futures, and indices via yfinance.

Two Parquet outputs:
  1. yf_prices  — daily OHLCV for all tickers
  2. comex_futures_curve — snapshot of gold futures contracts for curve construction

Schema (yf_prices, per row):
  source_date   DATE
  ticker        TEXT
  label         TEXT
  open          FLOAT
  high          FLOAT
  low           FLOAT
  close         FLOAT
  volume        FLOAT
  source_name   TEXT
  ingested_at   TEXT
"""

import pandas as pd
import yfinance as yf

from config.settings import (
    YF_TICKERS,
    COMEX_FUTURES_CONTRACTS,
    DEFAULT_START,
    DEFAULT_END,
)
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("yfinance_fetcher")


def fetch_prices(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    """Pull daily OHLCV for all configured tickers."""
    logger.info(f"Pulling yfinance prices for {list(YF_TICKERS.keys())} ...")

    raw = yf.download(
        tickers=list(YF_TICKERS.keys()),
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
        group_by="ticker",
    )

    frames = []
    for ticker, label in YF_TICKERS.items():
        try:
            if len(YF_TICKERS) == 1:
                df_t = raw.copy()
            else:
                df_t = raw[ticker].copy()

            df_t = df_t.reset_index()
            df_t.columns = [c.lower() for c in df_t.columns]
            df_t = df_t.rename(columns={"date": "source_date", "adj close": "close"})

            # Keep standard columns, tolerate missing ones
            keep = [c for c in ["source_date", "open", "high", "low", "close", "volume"] if c in df_t.columns]
            df_t = df_t[keep].dropna(subset=["close"])

            df_t["ticker"] = ticker
            df_t["label"] = label
            frames.append(df_t)
            logger.info(f"  {ticker}: {len(df_t):,} rows")
        except Exception as e:
            logger.error(f"  FAILED {ticker}: {e}")

    if not frames:
        raise RuntimeError("No yfinance tickers fetched.")

    combined = pd.concat(frames, ignore_index=True)
    combined = add_envelope(combined, source_name="yfinance")

    col_order = ["source_date", "ticker", "label", "open", "high", "low", "close", "volume", "source_name", "ingested_at"]
    col_order = [c for c in col_order if c in combined.columns]
    return combined[col_order]


def fetch_futures_curve(as_of: str = DEFAULT_END) -> pd.DataFrame:
    """
    Pull the COMEX gold futures curve — each row is one contract's last price.
    This gives a snapshot of the forward curve on the pull date.
    """
    logger.info("Pulling COMEX gold futures curve ...")
    rows = []
    for contract in COMEX_FUTURES_CONTRACTS:
        try:
            t = yf.Ticker(contract)
            hist = t.history(period="5d")
            if hist.empty:
                logger.warning(f"  No data for {contract}")
                continue
            last = hist.iloc[-1]
            rows.append({
                "source_date": hist.index[-1].date().isoformat(),
                "contract":    contract,
                "close":       round(float(last["Close"]), 2),
                "volume":      float(last.get("Volume", 0)),
            })
            logger.info(f"  {contract}: {rows[-1]['close']}")
        except Exception as e:
            logger.error(f"  FAILED {contract}: {e}")

    if not rows:
        logger.warning("No futures curve data fetched — contract codes may need updating.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = add_envelope(df, source_name="yfinance_comex_curve")
    return df


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    prices = fetch_prices(start, end)
    write_parquet(prices, source="etf_and_market", label="yf_prices")

    curve = fetch_futures_curve()
    if not curve.empty:
        write_parquet(curve, source="comex", label="comex_futures_curve")

    logger.info("yfinance fetcher done.")
    return prices, curve


if __name__ == "__main__":
    run()
