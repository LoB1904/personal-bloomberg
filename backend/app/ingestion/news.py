"""
Fase 1 — RSS news aggregator.

Feed attivi:
  - Bloomberg Markets: https://feeds.bloomberg.com/markets/news.rss
  - Financial Times:   https://www.ft.com/rss/home
  - Reuters Business:  https://feeds.reuters.com/reuters/businessNews

Deduplicazione su URL: non inserisce articoli già presenti in news_items.
Upsert su news_items con conflict su url.
Ticker matching: se il titolo/summary contiene il ticker o il nome azienda,
                 collega news_items.ticker_id (best-effort, nullable).

Uso:
    python -m app.ingestion.news
    python -m app.ingestion.news --max-per-feed 100
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import requests
import urllib3
import pandas as pd
from sqlalchemy import text

from app.core.db import get_engine, upsert_dataframe

urllib3.disable_warnings()
os.environ["CURL_CA_BUNDLE"] = ""
os.environ["REQUESTS_CA_BUNDLE"] = ""

logger = logging.getLogger(__name__)

# ── Feed configurati ─────────────────────────────────────────────────

RSS_FEEDS: dict[str, str] = {
    "bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "ft":        "https://www.ft.com/rss/home",
    "reuters":   "https://feeds.reuters.com/reuters/businessNews",
}

_SESSION = requests.Session()
_SESSION.verify = False
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
})


# ── Helpers ───────────────────────────────────────────────────────────

def _parse_date(entry) -> datetime | None:
    """Estrae published_at da un entry feedparser in formato UTC. Ritorna None se non parsificabile."""
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                pass
    logger.warning(f"News {getattr(entry, 'link', '?')}: data non parsificabile — articolo saltato")
    return None


def _clean_text(text: str | None, max_len: int = 2000) -> str | None:
    if not text:
        return None
    cleaned = " ".join(text.split())   # normalizza whitespace
    return cleaned[:max_len]


def _fetch_feed(source: str, url: str, max_items: int = 50) -> list[dict]:
    """
    Scarica e parsifica un singolo feed RSS.
    Ritorna lista di dict pronti per news_items.
    """
    logger.info(f"Fetch RSS: {source} ({url})")
    try:
        # feedparser usa urllib internamente — usiamo requests per SSL bypass
        r = _SESSION.get(url, timeout=20)
        if r.status_code != 200:
            logger.warning(f"{source}: HTTP {r.status_code}")
            return []
        feed = feedparser.parse(r.text)
    except Exception as e:
        logger.warning(f"{source}: errore fetch — {e}")
        return []

    entries = feed.entries[:max_items]
    rows = []

    for entry in entries:
        link = getattr(entry, "link", None)
        if not link:
            continue

        published_at = _parse_date(entry)
        if published_at is None:
            continue

        title   = _clean_text(getattr(entry, "title", None), 512)
        summary = _clean_text(
            getattr(entry, "summary", None) or getattr(entry, "description", None),
            2000
        )

        rows.append({
            "ticker_id":    None,   # matching best-effort in post-processing
            "source":       source,
            "title":        title,
            "summary":      summary,
            "url":          link,
            "published_at": published_at,
            "sentiment_score": None,   # calcolato in Fase 4 (AI layer)
        })

    logger.info(f"{source}: {len(rows)} articoli")
    return rows


# ── Ticker matching (best-effort) ─────────────────────────────────────

# Parole comuni che coincidono con ticker S&P500 — escluse dal matching per evitare falsi positivi
_TICKER_MATCH_BLACKLIST = frozenset({
    "IT", "AI", "NOW", "REAL", "ARE", "WELL", "ALL", "KEY", "DFS", "RE",
})


def _load_ticker_names(engine) -> dict[str, int]:
    """Carica {ticker: id} e {name_lower: id} per matching nel testo."""
    sql = text("SELECT id, ticker, name FROM ticker_universe WHERE is_active = TRUE")
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    mapping: dict[str, int] = {}
    for r in rows:
        if r.ticker.upper() not in _TICKER_MATCH_BLACKLIST:
            mapping[r.ticker.upper()] = r.id
        # Aggiungi il nome breve (primo token) per matching tipo "Apple" → AAPL
        short = r.name.split()[0].upper() if r.name else ""
        if len(short) > 3 and short not in mapping and short not in _TICKER_MATCH_BLACKLIST:
            mapping[short] = r.id
    return mapping


def _match_ticker(text_: str | None, ticker_map: dict[str, int]) -> int | None:
    """Cerca il primo ticker presente nel testo. Ritorna ticker_id o None."""
    if not text_:
        return None
    upper = text_.upper()
    for token, tid in ticker_map.items():
        if f" {token} " in upper or upper.startswith(token + " ") or upper.endswith(" " + token):
            return tid
    return None


# ── Deduplicazione ────────────────────────────────────────────────────

def _existing_urls(engine) -> set[str]:
    """Carica tutti gli URL già in news_items per deduplicazione."""
    sql = text("SELECT url FROM news_items")
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql).fetchall()
        return {r.url for r in rows}
    except Exception:
        return set()


# ── Main ─────────────────────────────────────────────────────────────

def fetch_news(engine=None, max_per_feed: int = 50) -> int:
    """
    Scarica tutti i feed RSS, deduplica per URL, fa upsert in news_items.
    Ritorna numero di nuovi articoli inseriti.
    """
    if engine is None:
        engine = get_engine()

    existing = _existing_urls(engine)
    ticker_map = _load_ticker_names(engine)
    logger.info(f"URL già in DB: {len(existing)} | ticker in mappa: {len(ticker_map)}")

    all_rows: list[dict] = []

    for source, url in RSS_FEEDS.items():
        rows = _fetch_feed(source, url, max_per_feed)
        for row in rows:
            if row["url"] in existing:
                continue
            # Best-effort ticker matching su titolo + summary
            combined = f"{row['title'] or ''} {row['summary'] or ''}"
            row["ticker_id"] = _match_ticker(combined, ticker_map)
            all_rows.append(row)
            existing.add(row["url"])   # evita duplicati tra feed diversi

    if not all_rows:
        logger.info("Nessun nuovo articolo da inserire")
        return 0

    df = pd.DataFrame(all_rows)
    n  = upsert_dataframe(engine, df, "news_items", ["url"])
    logger.info(f"DONE — {n} nuovi articoli in news_items")
    return n


# ── Entry point ───────────────────────────────────────────────────────

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="Fetch news da RSS feed (Bloomberg, FT, Reuters)")
    parser.add_argument("--max-per-feed", type=int, default=50,
                        help="Max articoli per feed (default 50)")
    args = parser.parse_args()
    fetch_news(max_per_feed=args.max_per_feed)
    return 0


if __name__ == "__main__":
    # Smoke test: verifica connessione e primo fetch Reuters (meno restrittivo)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    print("Test fetch Reuters RSS...")
    rows = _fetch_feed("reuters", RSS_FEEDS["reuters"], max_items=3)
    if rows:
        print(f"OK — {len(rows)} articoli. Primo: {rows[0]['title']}")
    else:
        print("KO — nessun articolo (proxy o feed non raggiungibile)")
    sys.exit(0)
