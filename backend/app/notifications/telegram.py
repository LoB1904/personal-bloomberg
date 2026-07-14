"""
Fase 4 — Alert giornaliero su Telegram.

Valuta l'universo single-name su TRE segnali combinati (logica in Python, non in AI):

  Segnale 1 — Convergenza multipla:
     quality_score > 60  AND  price > MA50  AND  momentum 3m > 0
  Segnale 2 — Qualità in correzione:
     quality_score > 65  AND  prezzo -8% nell'ultimo mese
     AND fondamentali solidi (FCF > 0  AND  debt/equity < 1.5)
  Segnale 3 — Leader settoriale in trend:
     quality_score > 55  AND  il settore ha l'ETF in LONG (screener speculative)

Scoring candidati:
  1 segnale  → debole (ignorato)
  2 segnali  → valido
  3 segnali  → forte (priorità massima)

Seleziona max 5 candidati (prima i forti). Se zero → nessun alert.
Sui candidati chiama Claude (claude-haiku-4-5) per la narrativa, poi invia su Telegram.

Uso:
    python -m app.notifications.telegram          # run completo
    python -m app.notifications.telegram --test   # invia solo un messaggio di test
    python -m app.notifications.telegram --dry-run # calcola e stampa, non invia
"""
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date

import pandas as pd
import requests
import urllib3
from dotenv import load_dotenv
from sqlalchemy import text

from app.core.db import get_engine
from app.core.config import ENV_FILE
from app.analytics.fundamental import FundamentalScreener
from app.analytics.technical import TechnicalIndicators

# TELEGRAM_* e ANTHROPIC_API_KEY sono letti via os.getenv: pydantic-settings
# carica .env solo dentro l'oggetto Settings, non in os.environ. Serve load_dotenv.
# In CI (env var iniettate dal workflow) il .env manca e questo è un no-op.
load_dotenv(ENV_FILE)

os.environ.setdefault("CURL_CA_BUNDLE", "")
os.environ.setdefault("REQUESTS_CA_BUNDLE", "")
urllib3.disable_warnings()

logger = logging.getLogger(__name__)

_CLAUDE_MODEL = "claude-haiku-4-5"
_MAX_CANDIDATES = 5
_SPECULATIVE_PROFILE = "speculative_trend_etf"

# Session verify=False per il MITM cert del proxy aziendale (innocua in CI)
_SESSION = requests.Session()
_SESSION.verify = False

# Mappa settore (stile ticker_universe.sector) → sector ETF SPDR.
# Serve al Segnale 3: un titolo è "leader settoriale in trend" se l'ETF
# del suo settore è nella shortlist LONG dello screener speculative.
_SECTOR_TO_ETF: dict[str, str] = {
    "technology":             "XLK",
    "health_care":            "XLV",
    "financials":             "XLF",
    "communication_services": "XLC",
    "consumer_discretionary": "XLY",
    "consumer_staples":       "XLP",
    "energy":                 "XLE",
    "industrials":            "XLI",
    "utilities":              "XLU",
    "real_estate":            "XLRE",
    "materials":              "XLB",
}


# ── Data gathering ────────────────────────────────────────────────────

def _fetch_candidate_tickers(engine) -> list[dict]:
    """Single-name attivi che hanno almeno uno snapshot fondamentale."""
    sql = text("""
        SELECT DISTINCT t.id, t.ticker, t.sector
        FROM ticker_universe t
        JOIN fundamentals_snapshot f ON f.ticker_id = t.id
        WHERE t.is_active = TRUE
          AND t.universe_group IN ('sp100', 'sp500', 'ftsemib', 'wildcard')
        ORDER BY t.ticker
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()
    return [{"id": r.id, "ticker": r.ticker, "sector": r.sector} for r in rows]


def _fetch_long_sectors(engine) -> set[str]:
    """
    Settori il cui ETF è in LONG oggi (ultima run screener speculative).
    Lo screener speculative salva solo ETF in LONG, quindi tutti i ticker
    presenti nella sua shortlist più recente sono in trend.
    """
    sql = text("""
        SELECT t.ticker
        FROM screener_results s
        JOIN ticker_universe t ON t.id = s.ticker_id
        WHERE s.screener_profile = :prof
          AND s.run_date = (
              SELECT MAX(run_date) FROM screener_results WHERE screener_profile = :prof
          )
    """)
    with engine.connect() as conn:
        long_etfs = {r.ticker for r in conn.execute(sql, {"prof": _SPECULATIVE_PROFILE}).fetchall()}

    etf_to_sector = {v: k for k, v in _SECTOR_TO_ETF.items()}
    return {etf_to_sector[e] for e in long_etfs if e in etf_to_sector}


def _fetch_price_signals(engine, ticker_id: int) -> dict | None:
    """
    Calcola price, MA50, return_3m (63d), return_1m (21d) da prices_daily.
    Ritorna None se dati insufficienti.
    """
    sql = text("""
        SELECT date, close
        FROM prices_daily
        WHERE ticker_id = :tid
        ORDER BY date DESC
        LIMIT 70
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql, {"tid": ticker_id}).fetchall()

    if len(rows) < 51:            # servono almeno 50 giorni per la MA50
        return None

    df = pd.DataFrame(rows, columns=["date", "close"]).sort_values("date")
    close = pd.to_numeric(df["close"], errors="coerce").dropna().reset_index(drop=True)
    if len(close) < 51:
        return None

    price = float(close.iloc[-1])
    ma50  = float(TechnicalIndicators.ma(close, 50).iloc[-1])
    ret_3m = (price / float(close.iloc[-63]) - 1) * 100 if len(close) >= 63 else None
    ret_1m = (price / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else None

    return {"price": price, "ma50": ma50, "return_3m": ret_3m, "return_1m": ret_1m}


# ── Signal evaluation ─────────────────────────────────────────────────

def evaluate_signals(
    quality: float | None,
    price_sig: dict | None,
    fund: dict | None,
    sector: str | None,
    long_sectors: set[str],
) -> list[str]:
    """
    Valuta i 3 segnali. Ritorna la lista dei segnali attivi (es. ['S1', 'S3']).
    """
    active: list[str] = []
    if quality is None:
        return active

    price   = price_sig.get("price")     if price_sig else None
    ma50    = price_sig.get("ma50")      if price_sig else None
    ret_3m  = price_sig.get("return_3m") if price_sig else None
    ret_1m  = price_sig.get("return_1m") if price_sig else None

    fcf = _f(fund.get("free_cash_flow")) if fund else None
    de  = _f(fund.get("debt_to_equity")) if fund else None

    # S1 — Convergenza multipla
    if (quality > 60 and price is not None and ma50 is not None
            and price > ma50 and ret_3m is not None and ret_3m > 0):
        active.append("S1")

    # S2 — Qualità in correzione (fondamentali solidi)
    if (quality > 65 and ret_1m is not None and ret_1m < -8
            and fcf is not None and fcf > 0
            and de is not None and de < 1.5):
        active.append("S2")

    # S3 — Leader settoriale in trend
    if quality > 55 and sector is not None and sector in long_sectors:
        active.append("S3")

    return active


def _f(v) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── Candidate selection ───────────────────────────────────────────────

def build_candidates(engine) -> list[dict]:
    """
    Valuta l'universo e ritorna i candidati VALIDI (>= 2 segnali),
    ordinati per forza (3 segnali prima) poi per quality_score,
    troncati a max 5.
    """
    screener     = FundamentalScreener(engine)
    tickers      = _fetch_candidate_tickers(engine)
    long_sectors = _fetch_long_sectors(engine)

    logger.info(f"Valuto {len(tickers)} candidati | settori in LONG: {sorted(long_sectors) or '—'}")

    candidates: list[dict] = []
    for t in tickers:
        tid = t["id"]
        q      = screener.quality_score(tid).get("quality_score")
        if q is None:
            continue
        fund   = screener._fetch_latest_fundamentals(tid)
        psig   = _fetch_price_signals(engine, tid)
        active = evaluate_signals(q, psig, fund, t["sector"], long_sectors)

        if len(active) >= 2:      # 1 segnale = debole, ignorato
            candidates.append({
                "ticker":        t["ticker"],
                "sector":        t["sector"],
                "quality_score": round(q, 1),
                "signals":       active,
                "strength":      "forte" if len(active) == 3 else "valido",
                "pe_ratio":      _round(fund.get("pe_ratio") if fund else None, 2),
                "roe":           _round(fund.get("roe") if fund else None, 3),
                "variazione_1m": _round(psig.get("return_1m") if psig else None, 2),
                "trend_signal":  "long" if psig and psig.get("price") and psig.get("ma50")
                                 and psig["price"] > psig["ma50"] else "flat",
            })

    # Forti prima (3 segnali), poi per quality_score decrescente
    candidates.sort(key=lambda c: (len(c["signals"]), c["quality_score"]), reverse=True)
    return candidates[:_MAX_CANDIDATES]


def _round(v, n):
    f = _f(v)
    return round(f, n) if f is not None else None


# ── Claude enrichment ─────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Sei un investment analyst. Ricevi una lista di titoli candidati con i loro "
    "dati fondamentali e tecnici gia' calcolati. Per ogni titolo scrivi 2-3 frasi "
    "che spiegano perche' rappresenta un'opportunita' in questo momento, citando i "
    "dati specifici forniti. Sii diretto e professionale. Non inventare dati non "
    "presenti. Formato output: un paragrafo per titolo, inizia sempre con il ticker "
    "in grassetto."
)


def enrich_with_claude(candidates: list[dict], today: date) -> str | None:
    """
    Chiama Claude (claude-haiku-4-5) per generare la narrativa sui candidati.
    Ritorna il testo o None se la key manca o la chiamata fallisce.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY assente — salto l'arricchimento Claude")
        return None

    try:
        import anthropic
        import httpx

        client = anthropic.Anthropic(
            api_key=api_key,
            http_client=httpx.Client(verify=False),   # proxy aziendale
        )
        user_msg = (
            f"Candidati di oggi ({today.isoformat()}):\n"
            + json.dumps(candidates, ensure_ascii=False, indent=2)
        )
        resp = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        return "".join(block.text for block in resp.content if block.type == "text")
    except Exception as e:
        logger.error(f"Claude API fallita: {e}")
        return None


# ── Message building ──────────────────────────────────────────────────

def _fallback_message(candidates: list[dict], today: date) -> str:
    """Messaggio costruito dai soli dati Python (se Claude non disponibile)."""
    lines = [f"📊 *Alert Investimenti — {today.isoformat()}*", ""]
    for c in candidates:
        emoji = "🟢" if c["strength"] == "forte" else "🔵"
        lines.append(
            f"{emoji} *{c['ticker']}* — quality {c['quality_score']} "
            f"({c['strength']}, segnali {'+'.join(c['signals'])})"
        )
        details = []
        if c["pe_ratio"] is not None:      details.append(f"PE {c['pe_ratio']}")
        if c["roe"] is not None:           details.append(f"ROE {c['roe']}")
        if c["variazione_1m"] is not None: details.append(f"var 1m {c['variazione_1m']:+.1f}%")
        if details:
            lines.append("   " + " · ".join(details))
        lines.append("")
    return "\n".join(lines).strip()


def _compose_message(candidates: list[dict], claude_text: str | None, today: date) -> str:
    if claude_text:
        header = f"📊 *Alert Investimenti — {today.isoformat()}*"
        footer = "_Personal Bloomberg_"
        return f"{header}\n\n{claude_text}\n\n{footer}"
    return _fallback_message(candidates, today)


# ── Telegram send ─────────────────────────────────────────────────────

def send_telegram(text_msg: str) -> bool:
    """
    Invia un messaggio Markdown su Telegram.
    Legge TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID da os.getenv().

    Il Markdown legacy di Telegram va in errore su caratteri comuni (underscore,
    asterischi sbilanciati) che il testo di Claude può contenere. Se l'invio
    Markdown fallisce con un errore di parsing, ritenta in testo semplice:
    meglio un messaggio senza grassetto che nessun messaggio.

    Ritorna True se inviato.
    """
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID assenti — messaggio non inviato")
        logger.info(f"Messaggio che sarebbe stato inviato:\n{text_msg}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _post(payload: dict) -> requests.Response:
        return _SESSION.post(url, json=payload, timeout=15)

    # Tentativo 1: Markdown
    r = _post({"chat_id": chat_id, "text": text_msg, "parse_mode": "Markdown"})
    if r.status_code == 200:
        logger.info("Messaggio Telegram inviato (Markdown)")
        return True

    # Tentativo 2: testo semplice (fallback su errore di parsing Markdown)
    logger.warning(f"Markdown rifiutato ({r.status_code}: {r.json().get('description','')}) "
                   "— ritento in testo semplice")
    r2 = _post({"chat_id": chat_id, "text": text_msg})
    if r2.status_code == 200:
        logger.info("Messaggio Telegram inviato (testo semplice)")
        return True

    logger.error(f"Invio Telegram fallito: {r2.status_code} {r2.json().get('description','')}")
    return False


# ── Orchestration ─────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    """
    Pipeline completa: valuta → seleziona → Claude → invia.
    Ritorna il numero di candidati inclusi nell'alert (0 = nessun alert).
    """
    engine     = get_engine()
    today      = date.today()
    candidates = build_candidates(engine)

    if not candidates:
        logger.info("Nessun candidato valido oggi — nessun alert inviato")
        return 0

    logger.info(f"{len(candidates)} candidati selezionati: "
                + ", ".join(f"{c['ticker']}({'+'.join(c['signals'])})" for c in candidates))

    claude_text = enrich_with_claude(candidates, today)
    message     = _compose_message(candidates, claude_text, today)

    if dry_run:
        print(message)
        logger.info("dry-run: messaggio non inviato")
        return len(candidates)

    send_telegram(message)
    return len(candidates)


def send_test_message() -> bool:
    """Invia un messaggio di test per verificare la connettività Telegram."""
    msg = (
        "✅ *Personal Bloomberg — test*\n\n"
        "Se leggi questo messaggio, bot token e chat id sono configurati "
        "correttamente. Gli alert giornalieri arriveranno qui."
    )
    return send_telegram(msg)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Alert investimenti su Telegram")
    parser.add_argument("--test",    action="store_true", help="Invia solo un messaggio di test")
    parser.add_argument("--dry-run", action="store_true", help="Calcola e stampa, non invia")
    args = parser.parse_args()

    if args.test:
        ok = send_test_message()
        print("Test inviato" if ok else "Test NON inviato (controlla TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return 0

    n = run(dry_run=args.dry_run)
    print(f"Alert: {n} candidati")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
