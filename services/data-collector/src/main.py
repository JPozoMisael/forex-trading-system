"""
Punto de entrada del servicio data-collector.
Recolecta velas de los pares configurados a intervalos regulares,
las guarda en TimescaleDB y emite un evento en Redis Pub/Sub por cada par actualizado.
"""
import signal
import sys
import time
import os

# Agregar la raíz del proyecto al PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ========== IMPORTAR DESDE SHARED ==========
from shared.mt5_client import MT5Client
from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus

from db_writer import DBWriter

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo data-collector...")
    running = False


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info(
        f"Iniciando data-collector | Pares: {settings.forex_pairs} | Granularidad: {settings.candle_granularity} | Intervalo: {settings.collector_poll_seconds}s"
    )

    # ========== USAR MT5Client ==========
    client = MT5Client()
    if not client.connect():
        log.error("No se pudo conectar a MT5. Saliendo...")
        return
    
    db = DBWriter()
    bus = EventBus()

    while running:
        for pair in settings.forex_pairs:
            if not running:
                break
            try:
                candles = client.get_latest_candles(
                    pair=pair,
                    granularity=settings.candle_granularity,
                    count=5,
                )
                if candles:
                    try:
                        db.write_candles(candles)
                    except Exception as db_err:
                        log.warning(f"No se pudo guardar en BD (¿servidor iniciando?): {db_err}")

                    # Publicamos la última vela completa al bus de eventos
                    latest_candle = candles[-1]
                    try:
                        bus.publish(
                            f"{settings.channel_market_candle}:{pair}",
                            latest_candle,
                        )
                    except Exception as bus_err:
                        log.debug(f"Aviso Redis al publicar vela de {pair}: {bus_err}")

            except Exception:
                log.exception(f"Fallo recolectando {pair}, se continúa con el siguiente par")

        # Pausa entre ciclos
        for _ in range(settings.collector_poll_seconds):
            if not running:
                break
            time.sleep(1)

    client.disconnect()
    db.close()
    bus.close()
    log.info("Servicio data-collector finalizado correctamente.")


if __name__ == "__main__":
    main()