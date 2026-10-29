"""
Calendario económico para el reporte: noticias de impacto alto de las monedas
que se siguen (Fed, PIB, inflación, bancos centrales), en hora local.

Fuente: el calendario semanal público de ForexFactory (JSON, sin clave). Es
solo información para el usuario: el modelo no usa noticias. Si la fuente
falla, el reporte sale igual con un aviso.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)

# Nombres en español de las noticias más frecuentes de impacto alto (el resto queda en inglés)
TITLES_ES = {
    "Federal Funds Rate": "Decisión de tasas de la Fed",
    "FOMC Statement": "Comunicado de la Fed",
    "FOMC Press Conference": "Conferencia de prensa de la Fed",
    "FOMC Meeting Minutes": "Actas de la Fed",
    "Fed Chair Powell Speaks": "Discurso del presidente de la Fed",
    "Non-Farm Employment Change": "Empleo no agrícola (nóminas)",
    "Unemployment Rate": "Tasa de desempleo",
    "Average Hourly Earnings m/m": "Salarios por hora (mensual)",
    "CPI m/m": "Inflación IPC (mensual)",
    "CPI y/y": "Inflación IPC (anual)",
    "Core CPI m/m": "Inflación IPC subyacente (mensual)",
    "Core PCE Price Index m/m": "Inflación PCE subyacente (mensual)",
    "Core PCE Price Index y/y": "Inflación PCE subyacente (anual)",
    "Advance GDP q/q": "PIB adelantado (trimestral)",
    "GDP q/q": "PIB (trimestral)",
    "Prelim GDP q/q": "PIB preliminar (trimestral)",
    "Retail Sales m/m": "Ventas minoristas (mensual)",
    "Core Retail Sales m/m": "Ventas minoristas subyacentes (mensual)",
    "ISM Manufacturing PMI": "PMI manufacturero ISM",
    "ISM Services PMI": "PMI de servicios ISM",
    "JOLTS Job Openings": "Vacantes de empleo JOLTS",
    "Unemployment Claims": "Solicitudes de subsidio por desempleo",
    "Main Refinancing Rate": "Decisión de tasas del BCE",
    "Monetary Policy Statement": "Comunicado de política monetaria",
    "ECB Press Conference": "Conferencia de prensa del BCE",
    "ECB President Lagarde Speaks": "Discurso de la presidenta del BCE",
    "BOJ Policy Rate": "Decisión de tasas del Banco de Japón",
    "BOJ Press Conference": "Conferencia de prensa del Banco de Japón",
    "BOJ Outlook Report": "Informe de perspectivas del Banco de Japón",
    "Official Bank Rate": "Decisión de tasas del Banco de Inglaterra",
    "Cash Rate": "Decisión de tasas del Banco de Australia",
    "Employment Change": "Cambio en el empleo",
}

IMPACT_ES = {"High": "alto", "Medium": "medio", "Low": "bajo"}


def currencies(pair: str) -> List[str]:
    """Monedas que mueven un instrumento: EUR_USD -> EUR y USD; el oro se mueve con el dólar."""
    base, quote = pair.upper().split("_")
    return ["USD"] if base == "XAU" else [base, quote]


def fetch_events(url: Optional[str] = None, get: Optional[Callable] = None) -> List[Dict[str, Any]]:
    import requests

    get = get or requests.get
    r = get(url or settings.econ_calendar_url, timeout=15, headers={"User-Agent": "forex-trading-system"})
    r.raise_for_status()
    return r.json()


def upcoming(events: List[Dict[str, Any]], pairs: List[str], now: datetime, hours: int = 24,
             impacts=("High",)) -> List[Dict[str, Any]]:
    """
    Noticias de las próximas `hours` horas (y las de la última hora, que aún
    mueven el precio) de las monedas de `pairs`, ordenadas por hora. Cada una
    con su hora UTC y los instrumentos a los que afecta.
    """
    by_currency: Dict[str, List[str]] = {}
    for pair in pairs:
        for cur in currencies(pair):
            by_currency.setdefault(cur, []).append(pair)
    out = []
    for e in events:
        try:
            when = datetime.fromisoformat(str(e["date"])).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        cur = str(e.get("country", "")).upper()
        if e.get("impact") not in impacts or cur not in by_currency:
            continue
        if not (now - timedelta(hours=1) <= when <= now + timedelta(hours=hours)):
            continue
        title = str(e.get("title", ""))
        out.append({"cuando": when, "moneda": cur, "titulo": TITLES_ES.get(title, title), "original": title,
                    "impacto": IMPACT_ES.get(e.get("impact"), e.get("impact")),
                    "prevision": e.get("forecast") or "-", "anterior": e.get("previous") or "-",
                    "afecta": by_currency[cur]})
    return sorted(out, key=lambda x: x["cuando"])


def calendar_for_report(pairs: List[str], now: datetime, fetch: Callable = fetch_events) -> Optional[List[Dict]]:
    """Noticias para el reporte; None si la fuente falló (el reporte lo indica y sale igual)."""
    if not settings.enable_econ_calendar:
        return []
    try:
        return upcoming(fetch(), pairs, now)
    except Exception as e:
        log.warning(f"No se pudo leer el calendario económico: {e}")
        return None
