"""
explore.py
Quick exploration of the bronze-layer parquet files.
Run from the ifwm_gold/ directory:
    python explore.py
"""

from pathlib import Path
import pandas as pd

RAW = Path("data/raw")

def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def subsection(title: str):
    print(f"\n  --- {title} ---")

def explore_dir(source: str):
    d = RAW / source
    files = sorted(d.glob("*.parquet"))
    if not files:
        print(f"  No parquet files found in {d}")
        return

    # Read all parquet files in the directory and combine
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    section(source.upper())

    print(f"\n  Files   : {[f.name for f in files]}")
    print(f"  Rows    : {len(df):,}")
    print(f"  Columns : {list(df.columns)}")

    subsection("Data types")
    print(df.dtypes.to_string())

    subsection("Date range")
    if "source_date" in df.columns:
        dates = pd.to_datetime(df["source_date"])
        print(f"  Earliest : {dates.min().date()}")
        print(f"  Latest   : {dates.max().date()}")
        print(f"  Unique dates : {dates.nunique():,}")

    subsection("Sample (first 5 rows)")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    pd.set_option("display.max_colwidth", 40)
    print(df.head().to_string(index=False))

    subsection("Nulls per column")
    nulls = df.isnull().sum()
    nulls = nulls[nulls > 0]
    if nulls.empty:
        print("  No nulls")
    else:
        print(nulls.to_string())

    # Source-specific summaries
    if source == "fred":
        subsection("Series coverage")
        summary = (
            df.groupby(["series_id", "label"])
              .agg(
                  rows=("value", "count"),
                  earliest=("source_date", "min"),
                  latest=("source_date", "max"),
                  latest_value=("value", "last"),
                  unit=("unit", "first"),
              )
              .reset_index()
        )
        print(summary.to_string(index=False))

    if source == "etf_and_market":
        subsection("Ticker coverage")
        summary = (
            df.groupby(["ticker", "label"])
              .agg(
                  rows=("close", "count"),
                  earliest=("source_date", "min"),
                  latest=("source_date", "max"),
                  latest_close=("close", "last"),
              )
              .reset_index()
        )
        print(summary.to_string(index=False))

    if source == "lbma" or source == "ibma":
        subsection("Fix type breakdown")
        if "fix_type" in df.columns:
            print(df["fix_type"].value_counts().to_string())
            subsection("Latest prices")
            latest = (
                df.sort_values("source_date")
                  .groupby("fix_type")
                  .last()
                  [["source_date", "usd"]]
            )
            print(latest.to_string())


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Auto-detect all source directories that have parquet files
    sources = [
        d.name for d in sorted(RAW.iterdir())
        if d.is_dir() and list(d.glob("*.parquet"))
    ]

    print(f"Found {len(sources)} data source(s): {sources}")

    for source in sources:
        try:
            explore_dir(source)
        except Exception as e:
            print(f"\n  ERROR exploring {source}: {e}")

    print(f"\n{'='*60}")
    print("  Done")
    print(f"{'='*60}\n")
