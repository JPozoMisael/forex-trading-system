# services/data-collector/src/main.py
import signal
import sys
import time
import os

# Agregar la raíz del proyecto al PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

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

    # ========== USAR OANDA EN PRODUCCIÓN ==========
    try:
        from oanda_client import OandaClient
        client = OandaClient()
        log.info("✅ Usando OANDA como fuente de datos")
    except Exception as e:
        log.error(f"Error cargando OANDA: {e}")
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
                        log.warning(f"No se pudo guardar en BD: {db_err}")

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

        for _ in range(settings.collector_poll_seconds):
            if not running:
                break
            time.sleep(1)

    db.close()
    bus.close()
    log.info("Servicio data-collector finalizado correctamente.")

if __name__ == "__main__":
    main()