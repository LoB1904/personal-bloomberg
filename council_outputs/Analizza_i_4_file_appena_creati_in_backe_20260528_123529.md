## Problema

Ho identificato **7 problemi critici e medi** che causerebbero fallimenti silenziosi, perdite di dati o vulnerabilità in produzione.

## Valutazione

### **CRITICO**

| File | Riga | Problema | Severità | Fix |
|------|------|----------|----------|-----|
| `fundamentals.py` | 206 | **Divisione per zero**: `gross_margin` calcola `GrossProfitTTM / RevenueTTM` senza verificare che `RevenueTTM > 0`. Se `RevenueTTM` è None o 0, genera `ZeroDivisionError` o `TypeError` silenzioso | CRITICO | Aggiungi guard: `if _f(h, "RevenueTTM") and _f(h, "RevenueTTM") > 0 else None` |
| `fundamentals.py` | 171 | **Perdita di dati silente**: `market_cap` moltiplicato per `1e6` solo se `_f(h, "MarketCapitalizationMln")` è truthy, ma il valore potrebbe essere 0 (legittimo per micro-cap). Usa `is not None` | CRITICO | Cambia: `_f(h, "MarketCapitalizationMln") is not None and _f(h, "MarketCapitalizationMln") * 1e6 or None` |
| `backfill.py` | 243 | **Race condition su checkpoint**: `_save_checkpoint()` ogni 10 ticker senza lock. Se due processi corrono in parallelo, il file viene sovrascritto parzialmente → perdita di completamenti | CRITICO | Aggiungi lock file con `fcntl` o usa atomic write con `Path.write_text(..., atomic=True)` |
| `fundamentals.py` | 282 | **Credenziali esposte in log**: `logger.info(f"EODHD crediti usati: {guard.used}/{guard._max}")` è OK, ma la funzione `_fetch_eodhd_fundamentals()` potrebbe loggare URL con `api_token` se aggiunto a params in debug | CRITICO | Non loggare mai `params` dict. Usa `logger.debug(f"EODHD {eodhd_ticker}: richiesta inviata")` senza params |

### **MEDIO**

| File | Riga | Problema | Severità | Fix |
|------|------|----------|----------|-----|
| `news.py` | 85 | **Parsing data fragile**: `_parse_date()` prova 3 attributi ma se tutti falliscono ritorna `datetime.utcnow()` senza warning. Articoli vecchi di mesi appariranno come "oggi" → distorsione timeline | MEDIO | Loga warning: `logger.warning(f"News URL {entry.get('link')}: data non parsificabile, usa fallback")` e ritorna `None` invece di `utcnow()` |
| `macro.py` | 137 | **Mancanza di error handling su Eurostat**: Se la risposta JSON non ha `dimension.time.category.index`, cattura `KeyError` ma non registra quale campo manca. Rende difficile il debug | MEDIO | Estendi il catch: `except (KeyError, TypeError, ValueError) as e:` e loga: `logger.error(f"Eurostat HICP: struttura {e} — risposta: {data.keys()}")` |
| `backfill.py` | 299 | **Incoerenza schema**: `prices_daily.adj_close` è `NUMERIC(20,8)` ma Yahoo Finance può ritornare `None` per alcuni ticker storici. Upsert fallisce se `adj_close` è NOT NULL nel codice ma NULL nel DB | MEDIO | Verifica schema: se `adj_close` è nullable, OK. Altrimenti aggiungi fallback: `adj_close = adj or close` |

### **BASSO**

| File | Riga | Problema | Severità | Fix |
|------|------|----------|----------|-----|
| `fundamentals.py` | 140 | **Suffix map incompleto**: `_SUFFIX_MAP` manca `.F` (Borsa Italiana secondaria), `.SW` (SIX Swiss), `.TO` (TSX Canada) → ticker come `VWCE.F` non vengono convertiti correttamente | BASSO | Aggiungi: `".F": "XETRA", ".SW": "XSWX", ".TO": "TO"` |
| `news.py` | 126 | **Ticker matching troppo greedy**: Cerca ` APPLE ` in testo ma non esclude acronimi come "APPLE Inc." → potrebbe matchare il nome invece del ticker. Inoltre non normalizza case | BASSO | Usa regex word boundary: `if re.search(rf'\b{token}\b', upper):` dopo `import re` |

## Rischi

### Fallimenti in Produzione
1. **fundamentals.py**: Gross margin con revenue=0 → eccezione non gestita, ingestion si ferma
2. **backfill.py**: Due job paralleli → file checkpoint corrotto, ticker duplicati o saltati
3. **news.py**: Date sbagliate → timeline distorta, alert non triggerano al momento giusto

### Perdita di Dati
- Market cap a 0 interpretato come "dato mancante" → record non inseriti
- Checkpoint race condition → ingestione riparte da zero, duplicati in DB

### Sicurezza
- API key potrebbe finire in log se aggiunta a params dict
- SSL bypass globale (`_SESSION.verify = False`) senza motivazione esplicita

## Decisione Finale

### **PATCH IMMEDIATO** (pre-produzione)

**fundamentals.py — Riga 206**
```python
# PRIMA (bug)
"gross_margin": _f(h, "GrossProfitTTM") and (
    (_f(h, "GrossProfitTTM") or 0) / (_f(h, "RevenueTTM") or 1)
),

# DOPO (fix)
"gross_margin": (
    lambda gp, rev: (gp / rev) if gp is not None and rev is not None and rev > 0 else None
)(_f(h, "GrossProfitTTM"), _f(h, "RevenueTTM"))
```

**fundamentals.py — Riga 171**
```python
# PRIMA
"market_cap": _f(h, "MarketCapitalizationMln") and _f(h, "MarketCapitalizationMln") * 1e6,

# DOPO
"market_cap": (
    _f(h, "MarketCapitalizationMln") * 1e6 
    if _f(h, "MarketCapitalizationMln") is not None 
    else None
),
```

**backfill.py — Riga 243 (aggiorna `_save_checkpoint`)**
```python
def _save_checkpoint(completed_ids: set[int]) -> None:
    try:
        temp_file = _CHECKPOINT_FILE.with_suffix(".tmp")
        temp_file.write_text(
            json.dumps({"completed_ids": sorted(completed_ids), "updated_at": date.today().isoformat()})
        )
        temp_file.replace(_CHECKPOINT_FILE)  # atomic on POSIX
    except Exception as e:
        logger.warning(f"Checkpoint non salvato: {e}")
```

**fundamentals.py — Riga 282 (rimuovi credenziali da log)**
```python
# PRIMA
logger.info(f"DONE — {n} fondamentali upsertati | EODHD crediti usati: {guard.used}/{guard._max}")

# DOPO (niente params sensibili)
logger.info(f"DONE — {n} fondamentali upsertati | crediti EODHD: {guard.used}/{guard._max}")
# E in _fetch_eodhd, non loggare mai `params`:
logger.debug(f"EODHD {eodhd_ticker}: GET /fundamentals/{eodhd_ticker}")  # NO params
```

**news.py — Riga 85 (fix date fallback)**
```python
def _parse_date(entry) -> datetime | None:  # ritorna None se non parsificabile
    """Estrae published_at da un entry feedparser in formato UTC."""
    for attr in ("published", "updated", "created"):
        raw = getattr(entry, attr, None)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc).replace(tzinfo=None)
            except Exception:
                pass
    logger.warning(f"News URL {entry.get('link')}: data non parsificabile, skip")
    return None

# E in _fetch_feed, filtra None:
rows = [r for r in rows if r["published_at"] is not None]
```

**macro.py — Riga 137 (error handling)**
```python
try:
    time_index: dict = data["dimension"]["time"]["category"]["index"]
    values_map: dict = data["value"]
except (KeyError, TypeError) as e:
    logger.error(f"Eurostat HICP: struttura inattesa ({e}) — resp keys: {list(data.keys())}")
    return pd.DataFrame()
```

### **POST-RELEASE** (entro 1 sprint)
- [ ] Aggiungi test unitari per divisione per zero in `_parse_eodhd()`
- [ ] Documenta perché `_SESSION.verify = False` (aggiungi commento o rimuovi se non necessario)
- [ ] Implementa distributed lock su checkpoint (Redis se disponibile, altrimenti lock file con timeout)
- [ ] Estendi `_SUFFIX_MAP` con mercati mancanti