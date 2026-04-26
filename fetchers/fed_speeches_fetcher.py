"""
fetchers/fed_speeches_fetcher.py  (v2)

Correct URL pattern confirmed from federalreserve.gov:
  Per-year index: /newsevents/speech/{YYYY}-speeches.htm
  Each speech   : /newsevents/speech/{speaker}{YYYYMMDD}a.htm

We iterate one page per year from start_year to end_year,
collect all speech links, then fetch each speech text.
"""

import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("fed_speeches_fetcher")

BASE_URL      = "https://www.federalreserve.gov"
# The Fed uses two URL formats depending on era:
#   Old (≤~2016): /newsevents/speech/{YYYY}speech.htm
#   New (≥~2017): /newsevents/speech/{YYYY}-speeches.htm
# We try both and use whichever works.
SPEECH_INDEX_NEW = f"{BASE_URL}/newsevents/speech/{{year}}-speeches.htm"
SPEECH_INDEX_OLD = f"{BASE_URL}/newsevents/speech/{{year}}speech.htm"
HEADERS       = {"User-Agent": "Mozilla/5.0 (research project)"}
REQUEST_DELAY = 1.5


def _get(url: str) -> BeautifulSoup:
    time.sleep(REQUEST_DELAY)
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return BeautifulSoup(r.text, "lxml")


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup.find_all(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def _parse_year_index(year: int, start: str, end: str) -> list[dict]:
    """Parse one year's speech index page, trying both URL formats."""
    urls_to_try = [
        SPEECH_INDEX_NEW.format(year=year),
        SPEECH_INDEX_OLD.format(year=year),
    ]
    soup = None
    for url in urls_to_try:
        logger.info(f"  Fetching speech index for {year}: {url}")
        try:
            soup = _get(url)
            break
        except Exception as e:
            logger.warning(f"  Could not load {url}: {e}")

    if soup is None:
        logger.warning(f"  No speech index found for {year} — skipping")
        return []

    results = []
    # Each speech is in an eventlist__event div or similar
    # Links follow pattern: /newsevents/speech/powell20240930a.htm
    for a in soup.find_all("a", href=re.compile(r"/newsevents/speech/\w+\d{8}a?\.htm")):
        href = a["href"]
        full_url = BASE_URL + href if href.startswith("/") else href

        # Extract date from URL
        m = re.search(r"(\d{8})", href)
        if not m:
            continue
        date_str = m.group(1)
        iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

        if iso < start or iso > end:
            continue

        # Extract speaker from surrounding context
        parent = a.find_parent()
        speaker = ""
        if parent:
            text = parent.get_text(" ", strip=True)
            # Speaker usually follows "Governor" or "Chair"
            sm = re.search(r"(Chair|Governor|Vice Chair|President)[^\n·•]+", text)
            if sm:
                speaker = sm.group(0).strip()[:80]

        results.append({
            "source_date": iso,
            "speaker":     speaker,
            "title":       a.get_text(strip=True)[:200],
            "url":         full_url,
        })

    logger.info(f"    → {len(results)} speeches found for {year}")
    return results


def _fetch_speech(meta: dict) -> dict | None:
    try:
        soup = _get(meta["url"])
        text = _extract_text(soup)
        if len(text) < 100:
            return None
        meta["text"] = text
        meta["word_count"] = len(text.split())
        return meta
    except Exception as e:
        logger.error(f"  Failed {meta['url']}: {e}")
        return None


def fetch(
    start: str = DEFAULT_START,
    end:   str = DEFAULT_END,
    max_speeches: int = 2000,
) -> pd.DataFrame:

    start_year = int(start[:4])
    end_year   = int(end[:4])

    # Collect all speech metadata across years
    all_meta = []
    for year in range(start_year, end_year + 1):
        all_meta.extend(_parse_year_index(year, start, end))

    logger.info(f"Total speeches to fetch: {len(all_meta)} (capped at {max_speeches})")
    all_meta = all_meta[:max_speeches]

    rows = []
    for i, meta in enumerate(all_meta):
        logger.info(f"  [{i+1}/{len(all_meta)}] {meta['source_date']} — {meta['title'][:50]} ...")
        doc = _fetch_speech(meta)
        if doc:
            rows.append(doc)

    if not rows:
        logger.warning("No Fed speeches fetched.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = add_envelope(df, source_name="fed_speeches")
    cols = ["source_date", "speaker", "title", "url",
            "text", "word_count", "source_name", "ingested_at"]
    return df[[c for c in cols if c in df.columns]]


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        return df
    path = write_parquet(df, source="fed_speeches", label="fed_speeches")
    logger.info(f"Fed speeches done — {len(df):,} speeches at {path}")
    return df


if __name__ == "__main__":
    run()