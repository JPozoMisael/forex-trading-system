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
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import pandas as pd
import psycopg2

from shared.config import instrument_config, load_trading_config, settings
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


def build_ml_strategies(cfg: dict, models_dir: Path) -> List[BaseStrategy]:
    """
    Un modelo por instrumento de `produccion.activos` (config.yaml) que el
    sistema reciba (FOREX_PAIRS), cada uno con sus costes. Solo esos operan.
    """
    from ml.serving import load_or_train

    gran = cfg["mercado"]["temporalidad"]
    if gran.upper() != settings.candle_granularity:
        log.error(f"Los modelos son de {gran} (config.yaml) pero el sistema recibe velas {settings.candle_granularity}: "
                  f"no generarán señales. Ajusta CANDLE_GRANULARITY.")
    activos = (cfg.get("produccion") or {}).get("activos") or [cfg["mercado"]["par"]]
    missing = [p for p in activos if p not in settings.forex_pairs]
    if missing:
        log.error(f"Instrumentos activos sin datos (faltan en FOREX_PAIRS): {missing}. Añádelos para que operen.")
    ignored = [p for p in settings.forex_pairs if p not in activos]
    if ignored:
        log.warning(f"Pares en FOREX_PAIRS sin modelo validado (solo se recogen velas, no operan): {ignored}")
    return [load_or_train(instrument_config(cfg, pair), models_dir, log=log.info)
            for pair in activos if pair in settings.forex_pairs]


def build_strategies(names: List[str], models_dir: Path = MODELS_DIR) -> List[BaseStrategy]:
    """Estrategias activas según ACTIVE_STRATEGIES. Un nombre desconocido es un error."""
    strategies: List[BaseStrategy] = []
    for name in names:
        if name == "ma_crossover":
            strategies.append(MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=200, use_trend_filter=True))
        elif name == "mean_reversion":
            strategies.append(MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14, rsi_oversold=30.0, rsi_overbought=70.0))
        elif name == "ml":
            strategies.extend(build_ml_strategies(load_trading_config(), models_dir))
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

            states = {}
            for strat in strategies:
                if not strat.supports(pair, candle.granularity):
                    continue
                try:
                    sig = strat.evaluate(df.iloc[-strat.lookback_window:], pair=pair)
                    states[strat.name] = dict(strat.last_state or {}, senal=sig.direction.value if sig else None)
                    if sig:
                        log.info(f"🔥 Señal detectada por [{strat.name}] en {pair}: {sig.direction.value.upper()} SL={sig.stop_loss} TP={sig.take_profit}")
                        publisher.publish(sig)
                except Exception:
                    states[strat.name] = {"error": True}
                    log.exception(f"Error ejecutando estrategia {strat.name} en {pair}")

            # Latido: el monitoring lo usa para saber que se evaluó la última vela y para el reporte diario
            try:
                bus.set_json(f"{settings.key_strategy_state_prefix}{pair}", {
                    "vela": candle.timestamp, "evaluada": datetime.now(timezone.utc), "estrategias": states,
                })
            except Exception as e:
                log.warning(f"No se pudo guardar el estado de {pair} en Redis: {e}")
    finally:
        publisher.close()
        bus.close()
        log.info("Servicio strategy-engine finalizado correctamente.")


if __name__ == "__main__":
    main()
