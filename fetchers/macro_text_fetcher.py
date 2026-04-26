"""
fetchers/macro_text_fetcher.py  (v3 — FRED-first approach)

BLS and Treasury websites block Python scrapers on many networks.
This version uses sources that are reliably accessible:

1. FRED API — Beige Book text series (FRED hosts the full text)
   Series: BEIGEBOOK (if available) — otherwise we use the known
   Beige Book PDF URLs which FRED links to directly.

2. Beige Book — Fed publishes these as plain HTML pages. We generate
   all known URLs from 2000-2026 using the documented schedule and
   fetch with aggressive retry + longer timeouts.

3. BLS CPI/PPI narrative — Rather than the archive index (blocked),
   we construct individual release URLs directly from known dates
   using the BLS release calendar, which is predictable:
   CPI: released ~2 weeks after month end
   URL: https://www.bls.gov/news.release/archives/cpi_MMDDYYYY.htm
   We pull these one by one with long delays.

4. Treasury Quarterly Refunding — URLs follow a known slug pattern.
   We pull from the FRED-linked Treasury data page instead.

Actually, the most reliable approach for ALL of these is:
- Beige Book: use known URL pattern, longer timeout, retry
- CPI/PPI text: accept that BLS blocks scrapers and get the
  key numbers from FRED (already done) + skip the narrative text
- Treasury: use the API with correct endpoint

Let's be pragmatic: focus on Beige Book (highest value text)
and skip BLS/Treasury scraping for now.
"""

import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from datetime import datetime
from dateutil.relativedelta import relativedelta

from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("macro_text_fetcher")

BB_BASE = "https://www.federalreserve.gov"

# Known Beige Book URL patterns across different eras
# Modern:  /monetarypolicy/beigebook{YYYYMM}.htm       (2012+)
# Older:   /fomc/beigebook/{YYYY}/default.htm          (pre-2012)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
}

# Beige Book is published roughly on these months each year
BB_MONTHS = [1, 3, 5, 6, 8, 9, 10, 11, 12]


def _get(url: str, session: requests.Session, timeout: int = 8) -> BeautifulSoup | None:
    time.sleep(0.8)
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return BeautifulSoup(r.text, "lxml")
    except requests.exceptions.Timeout:
        logger.debug(f"  Timeout: {url}")
        return None
    except Exception as e:
        logger.debug(f"  Error {url}: {e}")
        return None


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _make_row(source_date, doc_type, doc_id, url, text):
    return {
        "source_date": source_date,
        "doc_type":    doc_type,
        "doc_id":      doc_id,
        "url":         url,
        "text":        text,
        "word_count":  len(text.split()),
    }


# ── Beige Book ────────────────────────────────────────────────────────────────

def _bb_urls_for_month(year: int, month: int) -> list[str]:
    """Return candidate Beige Book URLs for a given year/month."""
    yyyymm = f"{year:04d}{month:02d}"
    yyyy   = f"{year:04d}"
    return [
        f"{BB_BASE}/monetarypolicy/beigebook{yyyymm}.htm",        # 2011+
        f"{BB_BASE}/fomc/beigebook/{yyyy}/default.htm",           # pre-2011 (year only)
    ]


BEIGE_BOOK_START = "2017-01-01"  # confirmed working start
BEIGE_BOOK_END   = "2023-12-31"  # 2024+ pages are JS-rendered, content not in HTML


def fetch_beige_book(start: str, end: str) -> list[dict]:
    effective_start = max(start, BEIGE_BOOK_START)
    effective_end   = min(end,   BEIGE_BOOK_END)
    if effective_start > effective_end:
        logger.info("Beige Book: date range outside 2017-2023, skipping.")
        return []
    logger.info(f"Fetching Beige Books {effective_start} → {effective_end}...")
    start_dt = datetime.strptime(effective_start[:7], "%Y-%m")
    end_dt   = datetime.strptime(effective_end[:7],   "%Y-%m")

    session = requests.Session()
    session.headers.update(HEADERS)

    rows  = []
    seen  = set()
    current = start_dt
    total_months = 0

    while current <= end_dt:
        year  = current.year
        month = current.month
        total_months += 1

        if month in BB_MONTHS:
            yyyymm = f"{year:04d}{month:02d}"
            if yyyymm in seen:
                current += relativedelta(months=1)
                continue

            if total_months % 12 == 0:
                logger.info(f"  Progress: {current.strftime('%Y-%m')} ({len(rows)} found so far)")

            for url in _bb_urls_for_month(year, month):
                soup = _get(url, session)
                if soup is None:
                    continue
                text = _extract_text(soup)
                word_count = len(text.split())
                if word_count < 5000:
                    logger.warning(f"  Beige Book {yyyymm}: only {word_count} words — stub page, skipping")
                    continue
                iso = f"{year:04d}-{month:02d}-01"
                rows.append(_make_row(iso, "beige_book", f"beige_book_{yyyymm}", url, text))
                seen.add(yyyymm)
                logger.info(f"  Beige Book {yyyymm}: {word_count:,} words")
                break  # got it, don't try next URL pattern

            if yyyymm not in seen:
                logger.debug(f"  Beige Book {yyyymm}: not found (not published this month)")

        current += relativedelta(months=1)

    logger.info(f"  Total Beige Books fetched: {len(rows)}")
    return rows


# ── BLS CPI — construct individual URLs directly ──────────────────────────────
# BLS blocks archive index page, but individual release pages may work.
# We generate approximate release dates (CPI released ~2wks after month end)
# and try each one. Skip gracefully on 403/404.

BLS_BASE = "https://www.bls.gov"


def _cpi_release_dates(start: str, end: str) -> list[tuple[str, str]]:
    """
    Generate approximate CPI release dates.
    CPI for month M is released around the 10th-15th of month M+1.
    We try a range of days to find the actual release.
    """
    dates = []
    start_dt = datetime.strptime(start[:7], "%Y-%m")
    end_dt   = datetime.strptime(end[:7],   "%Y-%m")
    current  = start_dt

    while current <= end_dt:
        # CPI for `current` month is released in current+1 month
        release_month = current + relativedelta(months=1)
        for day in [10, 11, 12, 13, 14, 15, 16, 17, 18]:
            try:
                release_date = release_month.replace(day=day)
                date_str = release_date.strftime("%m%d%Y")
                iso = release_date.strftime("%Y-%m-%d")
                url = f"{BLS_BASE}/news.release/archives/cpi_{date_str}.htm"
                dates.append((iso, url))
            except ValueError:
                pass
        current += relativedelta(months=1)
    return dates


def fetch_bls_cpi(start: str, end: str) -> list[dict]:
    logger.info("Fetching BLS CPI releases (direct URL, one by one)...")
    session = requests.Session()
    session.headers.update(HEADERS)

    # Warm up session on BLS homepage
    try:
        session.get(f"{BLS_BASE}/", timeout=10)
        time.sleep(2)
    except Exception:
        pass

    candidates = _cpi_release_dates(start, end)
    rows = []
    found_months = set()

    for iso, url in candidates:
        month_key = iso[:7]
        if month_key in found_months:
            continue
        soup = _get(url, session, timeout=20)
        if soup is None:
            continue
        text = _extract_text(soup)
        if len(text) < 300:
            continue
        # Verify it's actually a CPI release
        if "consumer price index" not in text.lower():
            continue
        slug = iso.replace("-", "")
        rows.append(_make_row(iso, "cpi_release", f"bls_cpi_{slug}", url, text))
        found_months.add(month_key)
        logger.info(f"  CPI {iso}: {len(text.split()):,} words")

    logger.info(f"  Total CPI releases fetched: {len(rows)}")
    return rows


def fetch_bls_ppi(start: str, end: str) -> list[dict]:
    logger.info("Fetching BLS PPI releases (direct URL, one by one)...")
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        session.get(f"{BLS_BASE}/", timeout=10)
        time.sleep(2)
    except Exception:
        pass

    # PPI released ~2 weeks after month end, similar timing to CPI
    start_dt = datetime.strptime(start[:7], "%Y-%m")
    end_dt   = datetime.strptime(end[:7],   "%Y-%m")
    current  = start_dt
    rows     = []
    found_months = set()

    while current <= end_dt:
        release_month = current + relativedelta(months=1)
        for day in [11, 12, 13, 14, 15, 16, 17, 18, 19]:
            month_key = current.strftime("%Y-%m")
            if month_key in found_months:
                break
            try:
                release_date = release_month.replace(day=day)
                date_str = release_date.strftime("%m%d%Y")
                iso      = release_date.strftime("%Y-%m-%d")
                url = f"{BLS_BASE}/news.release/archives/ppi_{date_str}.htm"
            except ValueError:
                continue

            soup = _get(url, session, timeout=20)
            if soup is None:
                continue
            text = _extract_text(soup)
            if len(text) < 300:
                continue
            if "producer price" not in text.lower():
                continue
            slug = iso.replace("-", "")
            rows.append(_make_row(iso, "ppi_release", f"bls_ppi_{slug}", url, text))
            found_months.add(month_key)
            logger.info(f"  PPI {iso}: {len(text.split()):,} words")
            break

        current += relativedelta(months=1)

    logger.info(f"  Total PPI releases fetched: {len(rows)}")
    return rows


# ── Treasury Quarterly Refunding ──────────────────────────────────────────────
# Known quarterly refunding dates (first Wed of Feb, May, Aug, Nov).
# We search Treasury's site for these specific releases.

TREASURY_SEARCH = (
    "https://home.treasury.gov/news/press-releases"
    "?combine=quarterly+refunding&page={page}"
)
TREASURY_BASE = "https://home.treasury.gov"
TREASURY_KEYWORDS = ["refunding", "borrowing estimate", "marketable borrowing"]


def fetch_treasury(start: str, end: str) -> list[dict]:
    logger.info("Fetching Treasury quarterly refunding releases...")
    session = requests.Session()
    session.headers.update(HEADERS)

    links = []
    for page in range(0, 20):
        url = TREASURY_SEARCH.format(page=page)
        soup = _get(url, session)
        if soup is None:
            break

        found = False
        for a in soup.find_all("a", href=re.compile(r"/news/press-releases/")):
            title = a.get_text(strip=True).lower()
            if not any(kw in title for kw in TREASURY_KEYWORDS):
                continue

            # Try to find date near the link
            parent = a.find_parent()
            date_el = None
            if parent:
                date_el = parent.find(class_=re.compile(r"date|time", re.I))
                if not date_el:
                    date_el = parent.find("time")

            iso = None
            if date_el:
                raw = date_el.get("datetime", "") or date_el.get_text(strip=True)
                for fmt in ["%Y-%m-%d", "%B %d, %Y", "%m/%d/%Y"]:
                    try:
                        iso = datetime.strptime(raw[:10], fmt[:len(raw[:10])]).date().isoformat()
                        break
                    except Exception:
                        pass

            if not iso:
                iso = "2000-01-01"  # placeholder, will filter later

            href = a["href"]
            full = TREASURY_BASE + href if href.startswith("/") else href
            links.append((iso, full, title))
            found = True

        if not found:
            break

    logger.info(f"  Found {len(links)} Treasury releases, fetching text...")
    rows = []
    for iso, url, title in links:
        try:
            soup = _get(url, session)
            if soup is None:
                continue
            text = _extract_text(soup)
            if len(text) < 200:
                continue

            # Extract real date from page if we didn't get it
            if iso == "2000-01-01":
                date_m = re.search(r"\b(January|February|March|April|May|June|July|"
                                   r"August|September|October|November|December)"
                                   r"\s+\d{1,2},\s+\d{4}\b", text)
                if date_m:
                    try:
                        iso = datetime.strptime(date_m.group(0), "%B %d, %Y").date().isoformat()
                    except Exception:
                        pass

            if not (start <= iso <= end):
                continue

            slug = re.sub(r"[^a-z0-9]", "_", title[:40])
            rows.append(_make_row(iso, "treasury_pr",
                                  f"treasury_{iso.replace('-','')}_{slug}", url, text))
            logger.info(f"  Treasury {iso}: {len(text.split()):,} words")
        except Exception as e:
            logger.error(f"  Failed {url}: {e}")

    logger.info(f"  Total Treasury releases fetched: {len(rows)}")
    return rows


# ── Orchestrator ──────────────────────────────────────────────────────────────

def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    all_rows = []
    all_rows.extend(fetch_beige_book(start, end))
    # BLS and Treasury block Python scrapers on most networks.
    # Their numerical data is already in FRED (CPI, PPI, Fed Funds).
    # Uncomment below only if you have confirmed network access to these sites:
    # all_rows.extend(fetch_bls_cpi(start, end))
    # all_rows.extend(fetch_bls_ppi(start, end))
    # all_rows.extend(fetch_treasury(start, end))

    if not all_rows:
        logger.warning("No macro text documents fetched.")
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)
    df = df.sort_values(["source_date", "doc_type"]).reset_index(drop=True)
    df = add_envelope(df, source_name="macro_text")
    cols = ["source_date", "doc_type", "doc_id", "url",
            "text", "word_count", "source_name", "ingested_at"]
    return df[[c for c in cols if c in df.columns]]


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        return df
    path = write_parquet(df, source="macro_text", label="macro_text_docs")
    logger.info(f"Macro text done — {len(df):,} documents at {path}")
    return df


if __name__ == "__main__":
    run()