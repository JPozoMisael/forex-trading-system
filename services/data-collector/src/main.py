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
from shared.market_client import MarketDataClient
from db_writer import DBWriter

log = get_logger(__name__)

running = True

def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo data-collector...")
    running = False


def build_market_client() -> MarketDataClient:
    """
    Instancia el cliente de datos de mercado según MARKET_DATA_PROVIDER.
    Permite cambiar de broker con una sola variable de entorno, sin tocar
    el resto del pipeline (db_writer, EventBus, estrategias).
    """
    provider = settings.market_data_provider

    if provider == "mt5":
        # Solo funciona corriendo nativo en Windows con el terminal MT5 abierto.
        from shared.mt5_client import MT5Client
        client = MT5Client()
        log.info(f"✅ Usando MT5 como fuente de datos (servidor: {settings.mt5_server})")

    elif provider == "oanda":
        # Único proveedor apto para el despliegue en Dokploy/Linux (API REST,
        # no requiere terminal de escritorio). Ver .env.production.example.
        from oanda_client import OandaClient
        client = OandaClient()
        log.info("✅ Usando OANDA como fuente de datos")

    else:
        raise ValueError(
            f"MARKET_DATA_PROVIDER='{provider}' no soportado. Usa 'mt5' (pruebas locales en Windows) "
            f"u 'oanda' (despliegue Dokploy/Linux), o agrega la rama correspondiente en build_market_client()."
        )

    if not client.connect():
        raise RuntimeError(
            f"No se pudo conectar al proveedor '{provider}'. Revisa las credenciales en .env "
            f"antes de continuar; el servicio no puede recolectar datos sin conexión."
        )
    return client


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info(
        f"Iniciando data-collector | Proveedor: {settings.market_data_provider} | Pares: {settings.forex_pairs} | "
        f"Granularidad: {settings.candle_granularity} | Intervalo: {settings.collector_poll_seconds}s"
    )

    client = build_market_client()
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