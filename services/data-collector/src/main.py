# services/data-collector/src/main.py
import signal
import sys
import time
import os

# Agregar la raíz del proyecto al PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

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
        # XM y cualquier broker MT5. Solo corre nativo en Windows con el terminal abierto.
        from shared.mt5_client import MT5Client
        client = MT5Client()
        log.info(f"✅ Usando MT5 como fuente de datos (servidor: {settings.mt5_server or 'el del terminal'})")

    elif provider == "oanda":
        from oanda_client import OandaClient
        client = OandaClient()
        log.info("✅ Usando OANDA como fuente de datos")

    elif provider == "dukascopy":
        # Gratis y sin cuenta; corre en Linux/Docker. Solo datos: con DRY_RUN=true se simula la ejecución.
        from shared.dukascopy import DukascopyClient
        client = DukascopyClient()
        log.info("✅ Usando Dukascopy como fuente de datos (sin credenciales)")

    else:
        raise ValueError(
            f"MARKET_DATA_PROVIDER='{provider}' no soportado. Usa 'dukascopy', 'mt5' (XM) u 'oanda', "
            f"o agrega la rama correspondiente en build_market_client()."
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

    # Última vela publicada por par: cada vela cerrada se publica UNA sola vez,
    # así el strategy-engine no reevalúa (ni duplica señales de) la misma vela.
    last_published = {}

    while running:
        for pair in settings.forex_pairs:
            if not running:
                break
            try:
                # Primera pasada: backfill para que las estrategias tengan historia (EMA 200).
                count = 5 if pair in last_published else settings.collector_backfill_candles
                candles = client.get_latest_candles(
                    pair=pair,
                    granularity=settings.candle_granularity,
                    count=count,
                )
                if not candles:
                    continue

                # Si la BD falla no publicamos: el strategy-engine lee la historia
                # desde la BD y evaluaría sin la vela nueva.
                db.write_candles(candles)

                latest_candle = candles[-1]
                if last_published.get(pair) == latest_candle.timestamp:
                    continue

                bus.publish(f"{settings.channel_market_candle}:{pair}", latest_candle)
                last_published[pair] = latest_candle.timestamp
                log.info(f"🕯️ Vela cerrada {pair} {latest_candle.timestamp:%Y-%m-%d %H:%M} UTC close={latest_candle.close}")

            except Exception:
                log.exception(f"Fallo recolectando {pair}, se continúa con el siguiente par")

        for _ in range(settings.collector_poll_seconds):
            if not running:
                break
            time.sleep(1)

    db.close()
    bus.close()
    if hasattr(client, "disconnect"):
        client.disconnect()
    log.info("Servicio data-collector finalizado correctamente.")

if __name__ == "__main__":
    main()
