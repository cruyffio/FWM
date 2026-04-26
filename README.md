# I-FWM Gold Data Fetcher — Bronze Layer

Fetches all Gold data sources into a standardised raw (bronze) layer.
Each fetcher writes a timestamped Parquet file to `data/raw/<source>/`.

## Data Sources

| Source | Type | Feed | API / Method |
|--------|------|------|--------------|
| FRED | Numerical | Real yield (DFII10), DXY (DTWEXBGS), CPI, Fed funds | `fredapi` |
| LBMA | Numerical | AM/PM gold price fix | CSV download |
| COMEX | Numerical | Futures curve, registered/eligible inventory | `yfinance` (futures) + Quandl/CFTC |
| ETF flows | Numerical | GLD AUM / shares outstanding | `yfinance` |
| VIX | Numerical | CBOE VIX index | `yfinance` |
| FOMC | Textual | Statements + minutes | Fed website scrape |
| Fed speeches | Textual | Governor speeches | Fed website scrape |
| GDELT | Textual | Gold-tagged headlines | GDELT API |

## Setup

```bash
pip install -r requirements.txt
cp config/secrets.example.yaml config/secrets.yaml
# fill in your FRED API key in secrets.yaml
python run_all.py
```

## Output structure

```
data/raw/
  fred/          YYYY-MM-DD_fred.parquet
  lbma/          YYYY-MM-DD_lbma_fix.parquet
  comex/         YYYY-MM-DD_comex_futures.parquet
               YYYY-MM-DD_comex_inventory.parquet
  etf/           YYYY-MM-DD_etf_gld.parquet
  vix/           YYYY-MM-DD_vix.parquet
  fomc/          YYYY-MM-DD_fomc_<type>.parquet
  fed_speeches/  YYYY-MM-DD_fed_speeches.parquet
  gdelt/         YYYY-MM-DD_gdelt_gold.parquet
```

Every Parquet file shares three mandatory envelope columns:
- `source_date`   — the date the observation belongs to (from the source)
- `ingested_at`   — UTC timestamp when your pipeline pulled it
- `source_name`   — string identifier of the feed
# FWM
