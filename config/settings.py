"""
config/settings.py
Central configuration for all Gold bronze-layer fetchers.
Adjust DEFAULT_START / DEFAULT_END for your desired history window.
"""

from datetime import date

# ── History window ──────────────────────────────────────────────────────────
DEFAULT_START: str = "2000-01-01"   # earliest date to pull
DEFAULT_END:   str = date.today().isoformat()   # pull up to today

# ── Raw data output root ────────────────────────────────────────────────────
RAW_DATA_ROOT: str = "data/raw"

# ── FRED series to pull ─────────────────────────────────────────────────────
# Each entry: (series_id, human_label, unit)
FRED_SERIES = [
    ("DFII10",    "real_yield_10y",          "pct"),        # 10-yr TIPS real yield
    ("DFII5",     "real_yield_5y",           "pct"),        # 5-yr TIPS real yield
    ("DTWEXBGS",  "dxy_broad_usd_index",     "index"),      # Broad USD index
    ("CPIAUCSL",  "cpi_all_urban",           "index"),      # CPI
    ("PCEPILFE",  "core_pce",                "index"),      # Core PCE
    ("FEDFUNDS",  "fed_funds_rate",          "pct"),        # Effective Fed funds
    ("DFF",       "fed_funds_daily",         "pct"),        # Daily fed funds
    ("BAMLH0A0HYM2", "hy_credit_spread",     "pct"),        # HY credit spread (risk proxy)
    ("VIXCLS",    "vix_close",               "index"),      # VIX from FRED
]

# ── yfinance tickers ────────────────────────────────────────────────────────
YF_TICKERS = {
    "GC=F":   "comex_gold_front_month",    # Gold front-month futures
    "GLD":    "etf_gld",                   # SPDR Gold Shares ETF
    "IAU":    "etf_iau",                   # iShares Gold ETF
    "^VIX":   "vix_index",                 # VIX (backup to FRED)
    "DX-Y.NYB": "dxy_futures",             # DXY futures (backup)
    "^TNX":   "us_10y_yield",              # 10-yr Treasury yield
    "^TYX":   "us_30y_yield",              # 30-yr Treasury yield
}

# COMEX gold futures contracts to pull for curve construction
COMEX_FUTURES_CONTRACTS = [
    "GCG25.CMX", "GCJ25.CMX", "GCM25.CMX",
    "GCQ25.CMX", "GCZ25.CMX", "GCG26.CMX",
]

# ── GDELT ───────────────────────────────────────────────────────────────────
# Keywords used to filter GDELT for Gold-relevant events.
GDELT_GOLD_KEYWORDS = [
    "gold price", "gold market", "gold demand", "gold supply",
    "COMEX gold", "LBMA gold", "gold futures", "gold ETF",
    "central bank gold", "gold reserve", "gold mining",
    "Federal Reserve rate", "real yield", "FOMC",
    "dollar index", "USD strength", "inflation expectations",
    "geopolitical risk", "safe haven",
]
GDELT_MAX_ARTICLES_PER_DAY: int = 50
GDELT_DEFAULT_START: str = "2015-01-01"

# ── FOMC scraping ───────────────────────────────────────────────────────────
FOMC_BASE_URL = "https://www.federalreserve.gov"
FOMC_STATEMENTS_URL = f"{FOMC_BASE_URL}/monetarypolicy/fomccalendars.htm"

# ── LBMA ────────────────────────────────────────────────────────────────────
LBMA_GOLD_URL = "https://www.lbma.org.uk/prices-and-data/precious-metal-prices#/"
# LBMA publishes a downloadable CSV — we use the direct data endpoint
LBMA_GOLD_CSV_URL = "https://prices.lbma.org.uk/secure/json/d_au.json"

# ── Logging ──────────────────────────────────────────────────────────────────
LOG_DIR: str = "logs"
LOG_LEVEL: str = "INFO"
