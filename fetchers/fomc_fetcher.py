"""
fetchers/fomc_fetcher.py  (v2)

Correct URL patterns confirmed from federalreserve.gov:
  Calendar : /monetarypolicy/fomccalendars.htm
  Statement: /newsevents/pressreleases/monetary{YYYYMMDD}a.htm
  Minutes  : /monetarypolicy/fomcminutes{YYYYMMDD}.htm

The calendar page lists all meetings with direct links to statements
and minutes. We parse those links, fetch each document, extract text.
"""

import re
import time
import requests
import pandas as pd
from bs4 import BeautifulSoup
from config.settings import DEFAULT_START, DEFAULT_END
from utils.io import add_envelope, get_logger, write_parquet

logger = get_logger("fomc_fetcher")

BASE_URL      = "https://www.federalreserve.gov"
CALENDAR_URL  = f"{BASE_URL}/monetarypolicy/fomccalendars.htm"
HEADERS       = {"User-Agent": "Mozilla/5.0 (research project)"}
REQUEST_DELAY = 2.0


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



# All FOMC meeting dates 2000-2020 (the date the statement was released).
# Statements: /newsevents/pressreleases/monetary{YYYYMMDD}a.htm
# Minutes:    /monetarypolicy/fomcminutes{YYYYMMDD}.htm
FOMC_HISTORICAL_DATES = [
    # 2000
    "20000202","20000321","20000516","20000628","20000822","20001003","20001115","20001219",
    # 2001
    "20010103","20010131","20010320","20010518","20010627","20010821","20010917","20011002",
    "20011106","20011211",
    # 2002
    "20020130","20020319","20020507","20020626","20020813","20020924","20021106","20021210",
    # 2003
    "20030129","20030318","20030506","20030625","20030812","20030916","20031028","20031209",
    # 2004
    "20040128","20040316","20040504","20040630","20040810","20040921","20041110","20041214",
    # 2005
    "20050202","20050322","20050503","20050630","20050809","20050920","20051101","20051213",
    # 2006
    "20060131","20060328","20060510","20060629","20060808","20060920","20061025","20061212",
    # 2007
    "20070131","20070321","20070509","20070628","20070807","20070918","20071031","20071211",
    # 2008
    "20080122","20080130","20080318","20080430","20080625","20080805","20080916","20081008",
    "20081029","20081216",
    # 2009
    "20090128","20090318","20090429","20090624","20090812","20090923","20091104","20091216",
    # 2010
    "20100127","20100316","20100428","20100623","20100810","20100921","20101103","20101214",
    # 2011
    "20110126","20110315","20110427","20110622","20110809","20110921","20111102","20111213",
    # 2012
    "20120125","20120313","20120425","20120620","20120801","20120913","20121024","20121212",
    # 2013
    "20130130","20130320","20130501","20130619","20130731","20130918","20131030","20131218",
    # 2014
    "20140129","20140319","20140430","20140618","20140730","20140917","20141029","20141217",
    # 2015
    "20150128","20150318","20150429","20150617","20150729","20150917","20151028","20151216",
    # 2016
    "20160127","20160316","20160427","20160615","20160727","20160921","20161102","20161214",
    # 2017
    "20170201","20170315","20170503","20170614","20170726","20170920","20171101","20171213",
    # 2018
    "20180131","20180321","20180502","20180613","20180801","20180926","20181108","20181219",
    # 2019
    "20190130","20190320","20190501","20190619","20190731","20190918","20191030","20191211",
    # 2020
    "20200129","20200303","20200315","20200429","20200610","20200729","20200916","20201105",
    "20201216",
]


def _discover_historical_links(start: str, end: str) -> list[dict]:
    """
    Generate FOMC document URLs directly from known meeting dates.
    No scraping needed — URLs follow deterministic patterns.
    """
    meetings = []
    for date_str in FOMC_HISTORICAL_DATES:
        iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
        if not (start <= iso <= end):
            continue

        # Statement
        stmt_url = f"{BASE_URL}/newsevents/pressreleases/monetary{date_str}a.htm"
        meetings.append({
            "meeting_date": iso,
            "doc_type":     "statement",
            "url":          stmt_url,
            "doc_id":       f"fomc_statement_{date_str}",
        })

        # Minutes
        min_url = f"{BASE_URL}/monetarypolicy/fomcminutes{date_str}.htm"
        meetings.append({
            "meeting_date": iso,
            "doc_type":     "minutes",
            "url":          min_url,
            "doc_id":       f"fomc_minutes_{date_str}",
        })

    logger.info(f"  Historical FOMC: generated {len(meetings)} document URLs for {start} → {end}")
    return meetings




def _discover_links(start: str, end: str) -> list[dict]:
    """
    Parse the FOMC calendar page and return all statement + minutes links
    within the date range.

    Statement links: /newsevents/pressreleases/monetary20240131a.htm
    Minutes links  : /monetarypolicy/fomcminutes20240320.htm
    """
    logger.info(f"Fetching FOMC calendar: {CALENDAR_URL}")
    soup = _get(CALENDAR_URL)

    meetings = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]

        # Match statement links: monetary{YYYYMMDD}a.htm
        m = re.search(r"monetary(\d{8})a\.htm", href, re.I)
        if m:
            date_str = m.group(1)
            iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            if start <= iso <= end and date_str not in seen:
                seen.add(date_str)
                full = BASE_URL + href if href.startswith("/") else href
                meetings.append({
                    "meeting_date": iso,
                    "doc_type":     "statement",
                    "url":          full,
                    "doc_id":       f"fomc_statement_{date_str}",
                })

        # Match minutes links: fomcminutes{YYYYMMDD}.htm
        m = re.search(r"fomcminutes(\d{8})\.htm", href, re.I)
        if m:
            date_str = m.group(1)
            iso = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
            if start <= iso <= end:
                full = BASE_URL + href if href.startswith("/") else href
                mid = f"fomc_minutes_{date_str}"
                if mid not in seen:
                    seen.add(mid)
                    meetings.append({
                        "meeting_date": iso,
                        "doc_type":     "minutes",
                        "url":          full,
                        "doc_id":       f"fomc_minutes_{date_str}",
                    })

    logger.info(f"  Found {len(meetings)} FOMC documents in {start} → {end}")
    return meetings


def _fetch_doc(meta: dict) -> dict | None:
    try:
        soup = _get(meta["url"])
        text = _extract_text(soup)
        if len(text) < 200:
            logger.warning(f"  Very short text for {meta['doc_id']}: {len(text)} chars")
        return {
            "source_date":  meta["meeting_date"],
            "meeting_date": meta["meeting_date"],
            "doc_type":     meta["doc_type"],
            "doc_id":       meta["doc_id"],
            "url":          meta["url"],
            "text":         text,
            "word_count":   len(text.split()),
        }
    except Exception as e:
        logger.error(f"  Failed {meta['doc_id']}: {e}")
        return None


def fetch(start: str = DEFAULT_START, end: str = DEFAULT_END) -> pd.DataFrame:
    # Discover from current calendar (2021+)
    links = _discover_links(start, end)
    # Discover from historical per-year pages (2006-2020)
    if int(start[:4]) <= 2020:
        links += _discover_historical_links(start, end)
    # Deduplicate by doc_id
    seen_ids = set()
    unique = []
    for m in links:
        if m["doc_id"] not in seen_ids:
            seen_ids.add(m["doc_id"])
            unique.append(m)
    links = unique
    logger.info(f"Total FOMC documents to fetch: {len(links)}")
    rows = []
    for i, meta in enumerate(links):
        logger.info(f"  [{i+1}/{len(links)}] {meta['doc_type']} {meta['meeting_date']} ...")
        doc = _fetch_doc(meta)
        if doc:
            rows.append(doc)

    if not rows:
        logger.warning("No FOMC documents fetched.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df = add_envelope(df, source_name="fomc")
    cols = ["source_date", "meeting_date", "doc_type", "doc_id",
            "url", "text", "word_count", "source_name", "ingested_at"]
    return df[[c for c in cols if c in df.columns]]


def run(start: str = DEFAULT_START, end: str = DEFAULT_END):
    df = fetch(start, end)
    if df.empty:
        return df
    path = write_parquet(df, source="fomc", label="fomc_documents")
    logger.info(f"FOMC done — {len(df):,} documents at {path}")
    return df


if __name__ == "__main__":
    run()