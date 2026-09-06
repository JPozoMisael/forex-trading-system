"""
Punto de entrada del servicio strategy-engine.
Escucha eventos de nuevas velas en Redis o consulta periódicamente TimescaleDB,
ejecuta las estrategias algorítmicas activas y publica las señales resultantes.
"""
import signal
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional
import pandas as pd
import psycopg2

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import Candle

from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from signal_publisher import SignalPublisher

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo strategy-engine...")
    running = False


def load_recent_candles(pair: str, granularity: str, limit: int = 250) -> Optional[pd.DataFrame]:
    """
    Carga las últimas `limit` velas desde TimescaleDB para un par y granularidad.
    """
    try:
        conn = psycopg2.connect(settings.postgres_dsn)
        query = """
            SELECT timestamp, open, high, low, close, volume
            FROM candles
            WHERE pair = %s AND granularity = %s
            ORDER BY timestamp DESC
            LIMIT %s;
        """
        df = pd.read_sql_query(query, conn, params=(pair, granularity, limit))
        conn.close()

        if df.empty:
            return None

        # Invertir para orden cronológico ascendente y setear timestamp como índice
        df = df.iloc[::-1].reset_index(drop=True)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df.set_index("timestamp", inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        log.warning(f"Error consultando velas de {pair} en base de datos: {e}")
        return None


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info("Iniciando strategy-engine...")

    # Instanciamos estrategias activas
    strategies = [
        MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=200, use_trend_filter=True),
        MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14, rsi_oversold=30.0, rsi_overbought=70.0),
    ]

    publisher = SignalPublisher()
    bus = EventBus()

    # Canales a escuchar
    candle_channels = [f"{settings.channel_market_candle}:{pair}" for pair in settings.forex_pairs]

    log.info(f"Estrategias activas: {[s.name for s in strategies]}")
    log.info(f"Monitoreando pares: {settings.forex_pairs}")

    # Modo fallback si Redis no tiene mensajes o en bucle de evaluación
    while running:
        try:
            for pair in settings.forex_pairs:
                if not running:
                    break

                df = load_recent_candles(pair=pair, granularity=settings.candle_granularity, limit=250)
                if df is None or len(df) < 30:
                    continue

                for strat in strategies:
                    try:
                        sig = strat.evaluate(df, pair=pair)
                        if sig:
                            log.info(f"🔥 Señal detectada por [{strat.name}] en {pair}: {sig.direction.value.upper()} SL={sig.stop_loss} TP={sig.take_profit}")
                            publisher.publish(sig)
                    except Exception as strat_err:
                        log.error(f"Error ejecutando estrategia {strat.name} en {pair}: {strat_err}")

            # Espera entre rondas de evaluación (sincronizada con el data collector)
            for _ in range(settings.collector_poll_seconds):
                if not running:
                    break
                time.sleep(1)

        except Exception as e:
            log.exception(f"Error en bucle de evaluación de estrategias: {e}")
            time.sleep(5)

    publisher.close()
    bus.close()
    log.info("Servicio strategy-engine finalizado correctamente.")


if __name__ == "__main__":
    main()
