"""
Fase 2 — Fundamental Screener per single-name (profilo Wealth).

Quality score (0-100):
  4 fattori, ciascuno normalizzato 0-25 tramite percentile rank sull'universo:
    ROE             — redditività equity (higher = better)
    ROIC            — efficienza capitale (higher = better)
    Debt/Equity     — leva finanziaria (INVERSO: lower = better)
    FCF Yield       — free_cash_flow / market_cap (higher = better)

GARP score (0-100):
  PEG ratio (PE / EPS growth): < 1.0 ottimo, < 2.0 buono
  Revenue growth vs PE: crescita > PE% = growth a prezzo ragionevole
  Combined: (peg_score + growth_score) / 2

Combined score: quality * 0.6 + garp * 0.4

Nota: il metodo _percentile_rank carica una volta sola tutti i fondamentali
dell'universo (lazy cache) — evita N query per N ticker.

Uso:
    python -m app.analytics.fundamental
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.core.db import get_engine

logger = logging.getLogger(__name__)

# Campi validi per _percentile_rank (whitelist anti-injection)
_VALID_RANK_FIELDS = frozenset({
    "roe", "roic", "debt_to_equity", "gross_margin",
    "operating_margin", "net_margin", "pe_ratio", "ps_ratio",
    "pb_ratio", "ev_ebitda", "revenue_growth_yoy", "eps_growth_yoy",
    "fcf_yield",   # campo computato aggiunto in _load_universe_fundamentals
})


class FundamentalScreener:
    """Screener Wealth: Quality + GARP score su single-name."""

    def __init__(self, engine=None):
        self.engine = engine or get_engine()
        self._universe_cache: list[dict] | None = None

    # ── Fetch dati ────────────────────────────────────────────────────

    def _fetch_latest_fundamentals(self, ticker_id: int) -> dict[str, Any] | None:
        """
        Carica il fondamentale più recente per ticker_id da fundamentals_snapshot.
        Ritorna dict con tutti i campi, o None se assente.
        """
        sql = text("""
            SELECT *
            FROM fundamentals_snapshot
            WHERE ticker_id = :tid
            ORDER BY snapshot_date DESC
            LIMIT 1
        """)
        with self.engine.connect() as conn:
            result = conn.execute(sql, {"tid": ticker_id})
            row    = result.fetchone()

        if row is None:
            return None

        fund = dict(row._mapping)

        # Aggiungi FCF yield come campo computato
        fcf = fund.get("free_cash_flow")
        mc  = fund.get("market_cap")
        fund["fcf_yield"] = (
            float(fcf) / float(mc)
            if fcf is not None and mc is not None and float(mc) > 0
            else None
        )
        return fund

    def _load_universe_fundamentals(self) -> list[dict]:
        """
        Carica l'ultimo snapshot per ogni ticker nell'universo (lazy, cachato).
        Usato da _percentile_rank per calcolare distribuzioni.
        """
        if self._universe_cache is not None:
            return self._universe_cache

        sql = text("""
            SELECT f.*
            FROM fundamentals_snapshot f
            INNER JOIN (
                SELECT ticker_id, MAX(snapshot_date) AS max_date
                FROM fundamentals_snapshot
                GROUP BY ticker_id
            ) latest
              ON f.ticker_id = latest.ticker_id
             AND f.snapshot_date = latest.max_date
        """)
        with self.engine.connect() as conn:
            result = conn.execute(sql)
            rows   = [dict(r._mapping) for r in result.fetchall()]

        # Aggiungi FCF yield computato per tutta l'universo
        for row in rows:
            fcf = row.get("free_cash_flow")
            mc  = row.get("market_cap")
            row["fcf_yield"] = (
                float(fcf) / float(mc)
                if fcf is not None and mc is not None and float(mc) > 0
                else None
            )

        self._universe_cache = rows
        logger.debug(f"Universe cache caricata: {len(rows)} snapshot")
        return rows

    def _percentile_rank(self, value: float | None, field: str) -> float:
        """
        Percentile rank di value nel campo field rispetto all'universo corrente.
        Ritorna un valore in [0.0, 25.0].
        """
        if value is None or field not in _VALID_RANK_FIELDS:
            return 0.0

        universe = self._load_universe_fundamentals()
        values   = [
            float(r[field])
            for r in universe
            if r.get(field) is not None
        ]

        if not values:
            return 0.0

        below = sum(1 for v in values if v < float(value))
        return (below / len(values)) * 25.0

    # ── Quality Score ─────────────────────────────────────────────────

    def quality_score(self, ticker_id: int) -> dict[str, Any]:
        """
        Quality score basato su 4 fattori, ognuno 0-25 (percentile rank).
        Somma = 0-100.

        Fattori:
          ROE            → higher = better
          ROIC           → higher = better
          Debt/Equity    → INVERSO (lower debt = higher score)
          FCF Yield      → higher = better
        """
        fund = self._fetch_latest_fundamentals(ticker_id)
        if fund is None:
            return {
                "ticker_id":      ticker_id,
                "quality_score":  None,
                "roe_score":      None,
                "roic_score":     None,
                "de_score":       None,
                "fcf_yield_score": None,
            }

        p_roe = self._percentile_rank(fund.get("roe"), "roe")
        p_roic = self._percentile_rank(fund.get("roic"), "roic")
        # Debt/equity: inverso — chi ha meno debito ha rank più alto
        p_de = 25.0 - self._percentile_rank(fund.get("debt_to_equity"), "debt_to_equity")
        p_fcf = self._percentile_rank(fund.get("fcf_yield"), "fcf_yield")

        q_score = p_roe + p_roic + p_de + p_fcf

        return {
            "ticker_id":       ticker_id,
            "quality_score":   round(q_score, 2),
            "roe_score":       round(p_roe, 2),
            "roic_score":      round(p_roic, 2),
            "de_score":        round(p_de, 2),
            "fcf_yield_score": round(p_fcf, 2),
        }

    # ── GARP Score ────────────────────────────────────────────────────

    def garp_score(self, ticker_id: int) -> dict[str, Any]:
        """
        GARP score (Growth At Reasonable Price).

        PEG ratio = PE / EPS growth:
          < 1.0 → ottimo (40-50 pt)
          1.0-2.0 → buono (20-40 pt)
          > 2.0 → sopravvalutato (< 20 pt)

        Revenue growth vs PE:
          rev_growth > PE% → crescita a prezzo ragionevole (+50 pt)
          altrimenti → +25 pt

        Score = (peg_score + growth_score) / 2  → 0-50 scalato a 0-100
        """
        fund = self._fetch_latest_fundamentals(ticker_id)
        if fund is None:
            return {"ticker_id": ticker_id, "garp_score": None, "peg_ratio": None}

        pe         = fund.get("pe_ratio")
        eps_growth = fund.get("eps_growth_yoy")
        rev_growth = fund.get("revenue_growth_yoy")

        # Serve almeno PE per calcolare GARP
        if pe is None:
            return {
                "ticker_id":   ticker_id,
                "garp_score":  None,
                "peg_ratio":   None,
                "pe_ratio":    None,
                "rev_growth":  rev_growth,
            }

        pe         = float(pe)
        eps_growth = float(eps_growth) if eps_growth is not None else None
        rev_growth = float(rev_growth) if rev_growth is not None else None

        # PEG ratio (None se EPS growth <= 0)
        peg = None
        if eps_growth is not None and eps_growth > 0:
            peg = pe / eps_growth

        # peg_score (0-50)
        if peg is None:
            peg_score = 0.0
        elif peg < 1.0:
            peg_score = 50.0 - peg * 10.0    # 40-50 pt
        elif peg < 2.0:
            peg_score = 40.0 - (peg - 1.0) * 20.0   # 20-40 pt
        else:
            peg_score = max(0.0, 20.0 - (peg - 2.0) * 5.0)

        # growth_score (0-50): crescita > PE% è segnale GARP
        if rev_growth is not None and pe > 0 and rev_growth > (pe / 100.0):
            growth_score = 50.0
        else:
            growth_score = 25.0 if rev_growth is not None and rev_growth > 0 else 0.0

        garp = (peg_score + growth_score) / 2.0

        return {
            "ticker_id":  ticker_id,
            "garp_score": round(garp, 2),
            "peg_ratio":  round(peg, 3) if peg is not None else None,
            "pe_ratio":   round(pe, 2),
            "rev_growth": round(rev_growth, 4) if rev_growth is not None else None,
        }

    # ── Combined Score ────────────────────────────────────────────────

    def combined_score(self, ticker_id: int) -> dict[str, Any]:
        """
        Combina quality (60%) + GARP (40%).
        Ritorna None se entrambi i component sono None.
        """
        q = self.quality_score(ticker_id)
        g = self.garp_score(ticker_id)

        q_val = q.get("quality_score")
        g_val = g.get("garp_score")

        if q_val is None and g_val is None:
            return {
                "ticker_id":      ticker_id,
                "combined_score": None,
                "quality_score":  None,
                "garp_score":     None,
            }

        # Se uno solo è disponibile, usa solo quello
        if q_val is None:
            combined = g_val
        elif g_val is None:
            combined = q_val
        else:
            combined = q_val * 0.6 + g_val * 0.4

        return {
            "ticker_id":      ticker_id,
            "combined_score": round(combined, 2),
            "quality_score":  q_val,
            "garp_score":     g_val,
        }


# ── Smoke test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    engine    = get_engine()
    screener  = FundamentalScreener(engine)

    # Verifica che la tabella non sia vuota
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        count = conn.execute(_text("SELECT COUNT(*) FROM fundamentals_snapshot")).scalar()

    if count == 0:
        print("AVVISO: fundamentals_snapshot e' vuota.")
        print("Esegui prima: python -m app.ingestion.fundamentals --ticker AAPL")
        print("(richiede EODHD_API_KEY o FMP_API_KEY nel .env)")
        print()
        print("Verifica che la struttura del modulo sia corretta...")

        # Testa almeno che le classi e i metodi esistano e non crashino
        result = screener.combined_score(1)
        assert result["combined_score"] is None, "combined_score deve essere None senza dati"
        assert result["ticker_id"] == 1

        result_q = screener.quality_score(1)
        assert result_q["quality_score"] is None

        result_g = screener.garp_score(1)
        assert result_g["garp_score"] is None

        print("OK -- metodi esistono e gestiscono tabella vuota correttamente.")
        sys.exit(0)

    # Se ci sono dati: testa su 3 ticker reali
    from sqlalchemy import text as _text
    with engine.connect() as conn:
        rows = conn.execute(_text(
            "SELECT DISTINCT ticker_id FROM fundamentals_snapshot ORDER BY ticker_id LIMIT 3"
        )).fetchall()
    test_ids = [r[0] for r in rows]

    print(f"Trovati {count} snapshot — test su ticker_id: {test_ids}\n")

    for tid in test_ids:
        result = screener.combined_score(tid)
        q      = result.get("quality_score")
        g      = result.get("garp_score")
        c      = result.get("combined_score")
        print(f"ticker_id={tid}:")
        print(f"  Quality  : {q:.1f}/100" if q is not None else "  Quality  : N/A")
        print(f"  GARP     : {g:.1f}/100" if g is not None else "  GARP     : N/A")
        print(f"  Combined : {c:.1f}/100" if c is not None else "  Combined : N/A")
        print()

    print("All OK -- FundamentalScreener operativo.")
