"""
Punto de entrada del servicio strategy-engine.
Consume los eventos de velas cerradas que publica el data-collector, carga la
historia desde TimescaleDB, ejecuta las estrategias activas y publica las señales.

Cada vela se evalúa una sola vez por par: así una misma condición (p.ej. un
cruce de medias) no genera señales repetidas.
"""
import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import pandas as pd
import psycopg2

from shared.config import load_trading_config, settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import Candle

from strategies.base_strategy import BaseStrategy
from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from signal_publisher import SignalPublisher

log = get_logger(__name__)

MODELS_DIR = Path(ROOT) / "models"

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo strategy-engine...")
    running = False


def load_recent_candles(pair: str, granularity: str, limit: int = 300) -> Optional[pd.DataFrame]:
    """
    Carga las últimas `limit` velas desde TimescaleDB para un par y granularidad.
    """
    try:
        conn = psycopg2.connect(settings.postgres_dsn)
        try:
            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM candles
                WHERE pair = %s AND granularity = %s AND complete
                ORDER BY timestamp DESC
                LIMIT %s;
            """
            with conn.cursor() as cur:
                cur.execute(query, (pair, granularity, limit))
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return None

        # Invertir para orden cronológico ascendente y setear timestamp como índice
        df = pd.DataFrame(rows[::-1], columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        df.set_index("timestamp", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        log.warning(f"Error consultando velas de {pair} en base de datos: {e}")
        return None


def build_strategies(names: List[str], models_dir: Path = MODELS_DIR) -> List[BaseStrategy]:
    """Estrategias activas según ACTIVE_STRATEGIES. Un nombre desconocido es un error."""
    strategies: List[BaseStrategy] = []
    for name in names:
        if name == "ma_crossover":
            strategies.append(MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=200, use_trend_filter=True))
        elif name == "mean_reversion":
            strategies.append(MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14, rsi_oversold=30.0, rsi_overbought=70.0))
        elif name == "ml":
            from ml.serving import load_or_train

            cfg = load_trading_config()
            strat = load_or_train(cfg, models_dir, log=log.info)
            pair, gran = cfg["mercado"]["par"], cfg["mercado"]["temporalidad"]
            if pair not in settings.forex_pairs or gran.upper() != settings.candle_granularity:
                log.error(f"El modelo es de {pair} {gran} (config.yaml) pero el sistema recibe {settings.forex_pairs} "
                          f"{settings.candle_granularity}: no generará señales. Ajusta FOREX_PAIRS y CANDLE_GRANULARITY.")
            strategies.append(strat)
        else:
            raise ValueError(f"Estrategia desconocida '{name}' en ACTIVE_STRATEGIES. Opciones: ml, ma_crossover, mean_reversion")
    if not strategies:
        raise ValueError("ACTIVE_STRATEGIES está vacío")
    return strategies


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info("Iniciando strategy-engine...")

    strategies = build_strategies(settings.active_strategies)
    history_needed = max(s.lookback_window for s in strategies)

    bus = EventBus()
    publisher = SignalPublisher(bus=bus)

    candle_channels = [f"{settings.channel_market_candle}:{pair}" for pair in settings.forex_pairs]
    last_evaluated: Dict[str, datetime] = {}

    log.info(f"Estrategias activas: {[s.name for s in strategies]}")
    log.info(f"Monitoreando pares: {settings.forex_pairs}")

    try:
        for channel, data in bus.subscribe(*candle_channels, group="strategy-engine", should_stop=lambda: not running):
            try:
                candle = Candle(**data)
            except Exception as parse_err:
                log.warning(f"Vela inválida en '{channel}': {parse_err}")
                continue

            pair = candle.pair
            if pair in last_evaluated and candle.timestamp <= last_evaluated[pair]:
                continue  # vela ya evaluada

            df = load_recent_candles(pair=pair, granularity=candle.granularity, limit=history_needed)
            if df is None or df.index[-1] != pd.Timestamp(candle.timestamp):
                log.warning(f"La BD aún no tiene la vela {candle.timestamp} de {pair}; se omite la evaluación.")
                continue
            last_evaluated[pair] = candle.timestamp

            for strat in strategies:
                if not strat.supports(pair, candle.granularity):
                    continue
                try:
                    sig = strat.evaluate(df.iloc[-strat.lookback_window:], pair=pair)
                    if sig:
                        log.info(f"🔥 Señal detectada por [{strat.name}] en {pair}: {sig.direction.value.upper()} SL={sig.stop_loss} TP={sig.take_profit}")
                        publisher.publish(sig)
                except Exception:
                    log.exception(f"Error ejecutando estrategia {strat.name} en {pair}")
    finally:
        publisher.close()
        bus.close()
        log.info("Servicio strategy-engine finalizado correctamente.")


if __name__ == "__main__":
    main()
