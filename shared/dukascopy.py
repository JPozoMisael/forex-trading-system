"""
Datos de mercado de Dukascopy (gratis, sin cuenta ni token).

Única implementación de la descarga, compartida por el backtester
(backtesting/src/data_loader.py) y el data-collector (MARKET_DATA_PROVIDER=dukascopy).
Las velas son de precio MEDIO, calculado desde bid y ask (como OANDA price="M"),
que es la convención del backtester y del modelo.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from shared.config import GRANULARITY_MINUTES
from shared.fx import get_pip_size, normalize_pair
from shared.logger import get_logger
from shared.market_client import MarketDataClient
from shared.models import Candle

log = get_logger(__name__)

# Granularidad del sistema -> constante de dukascopy-python
DUKASCOPY_INTERVALS = {
    "M1": "INTERVAL_MIN_1", "M5": "INTERVAL_MIN_5", "M15": "INTERVAL_MIN_15", "M30": "INTERVAL_MIN_30",
    "H1": "INTERVAL_HOUR_1", "H4": "INTERVAL_HOUR_4", "D": "INTERVAL_DAY_1", "D1": "INTERVAL_DAY_1",
}


def _dukascopy():
    import dukascopy_python as dk

    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)
    return dk


def fetch_mid_candles(pair: str, granularity: str, start: datetime, end: datetime) -> pd.DataFrame:
    """
    Velas de precio medio entre `start` y `end`, con el spread real al cierre
    de cada vela en pips. Una llamada por año: la librería devuelve como
    máximo 30.000 velas por petición. Puede incluir la vela en formación.
    """
    dk = _dukascopy()
    instrument = normalize_pair(pair).replace("_", "/")
    interval = getattr(dk, DUKASCOPY_INTERVALS[granularity.upper()])

    frames = []
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(end, chunk_start + timedelta(days=365))
        bid = dk.fetch(instrument, interval, dk.OFFER_SIDE_BID, chunk_start, chunk_end)
        ask = dk.fetch(instrument, interval, dk.OFFER_SIDE_ASK, chunk_start, chunk_end)
        if not bid.empty and not ask.empty:
            ask = ask.reindex(bid.index)
            mid = (bid[["open", "high", "low", "close"]] + ask[["open", "high", "low", "close"]]) / 2
            mid["volume"] = bid["volume"].astype(float)
            mid["spread_pips"] = (ask["close"] - bid["close"]) / get_pip_size(pair)
            frames.append(mid.dropna())
            log.info(f"Dukascopy {pair} {granularity}: {len(mid)} velas de {chunk_start:%Y-%m-%d} a {chunk_end:%Y-%m-%d}")
        chunk_start = chunk_end

    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "timestamp"
    return df[~df.index.duplicated(keep="last")].sort_index()


def only_closed(df: pd.DataFrame, granularity: str, now: datetime) -> pd.DataFrame:
    """Descarta la vela que todavía no cerró."""
    bar = pd.Timedelta(minutes=GRANULARITY_MINUTES[granularity.upper()])
    return df[df.index + bar <= pd.Timestamp(now)]


class DukascopyClient(MarketDataClient):
    """
    Fuente de datos para el pipeline en vivo (data-collector). Corre en Linux y
    Docker, sin credenciales. Solo da datos: no ejecuta órdenes (con
    DRY_RUN=true el execution-engine simula).
    """

    def connect(self) -> bool:
        try:
            now = datetime.now(timezone.utc)
            df = fetch_mid_candles("EUR_USD", "H1", now - timedelta(days=5), now)
        except Exception as e:
            log.error(f"No se pudo conectar con Dukascopy: {e}")
            return False
        if df.empty:
            log.error("Dukascopy no devolvió velas recientes de EUR_USD.")
            return False
        log.info(f"✅ Conectado a Dukascopy (última vela EUR_USD H1: {df.index[-1]:%Y-%m-%d %H:%M} UTC)")
        return True

    def get_latest_candles(self, pair: str, granularity: str = "H1", count: int = 10) -> List[Candle]:
        """Últimas `count` velas CERRADAS (la vela en formación se descarta)."""
        now = datetime.now(timezone.utc)
        minutes = GRANULARITY_MINUTES[granularity.upper()]
        # Margen para fines de semana y feriados: 1.6x las velas pedidas + 4 días
        start = now - timedelta(minutes=minutes * count * 1.6) - timedelta(days=4)
        df = only_closed(fetch_mid_candles(pair, granularity, start, now), granularity, now).tail(count)
        return [
            Candle(
                pair=normalize_pair(pair),
                granularity=granularity.upper(),
                timestamp=ts.to_pydatetime(),
                open=float(row.open),
                high=float(row.high),
                low=float(row.low),
                close=float(row.close),
                volume=int(round(row.volume)),
                complete=True,
            )
            for ts, row in df.iterrows()
        ]

    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        """Último bid/ask conocido (cierre de la última vela de 1 minuto)."""
        try:
            dk = _dukascopy()
            now = datetime.now(timezone.utc)
            instrument = normalize_pair(pair).replace("_", "/")
            bid = dk.fetch(instrument, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_BID, now - timedelta(hours=3), now)
            ask = dk.fetch(instrument, dk.INTERVAL_MIN_1, dk.OFFER_SIDE_ASK, now - timedelta(hours=3), now)
            if bid.empty or ask.empty:
                return None
            b, a = float(bid["close"].iloc[-1]), float(ask["close"].iloc[-1])
            return {"bid": b, "ask": a, "spread": a - b, "tradeable": True,
                    "timestamp": pd.Timestamp(bid.index[-1]).to_pydatetime()}
        except Exception:
            log.exception(f"Error obteniendo precio Dukascopy para {pair}")
            return None
