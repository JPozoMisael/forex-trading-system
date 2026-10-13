"""
Punto de entrada del servicio execution-engine.

Es el único servicio que habla con el broker para operar. En un solo hilo
(MT5 no garantiza ser thread-safe):
  - ejecuta las órdenes aprobadas que llegan por Redis Streams,
  - publica cada 5 s el snapshot de cuenta y las especificaciones de símbolos
    que usa el risk-manager para dimensionar y aplicar el circuit breaker,
  - reconcilia cada 10 s las posiciones contra el broker.
"""
import os
import signal
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import OrderRequest

from brokers import Broker, MT5Broker, SimulatedBroker
from order_manager import OrderManager, OrderRepository
from reconciliation import OrderReconciliation

log = get_logger(__name__)

running = True

SNAPSHOT_EVERY_SECONDS = 5
RECONCILE_EVERY_SECONDS = 10
STATE_TTL_SECONDS = 300


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo execution-engine...")
    running = False


def build_broker() -> Broker:
    if settings.dry_run:
        return SimulatedBroker()
    if settings.market_data_provider != "mt5":
        raise SystemExit(
            f"La ejecución real solo está implementada para MT5 (XM). Con MARKET_DATA_PROVIDER="
            f"'{settings.market_data_provider}' usa DRY_RUN=true."
        )
    return MT5Broker()


def publish_state(broker: Broker, bus: EventBus):
    """Snapshot de cuenta + especificaciones de símbolos para el risk-manager."""
    account = broker.account_summary()
    if account is None:
        log.warning("No se pudo obtener el estado de la cuenta; el risk-manager rechazará señales hasta recuperarlo.")
        return

    # Equity al inicio del día UTC: se fija la primera vez que se ve el día
    # (sobrevive a reinicios porque vive en Redis).
    day_key = f"state:day_start:{account.source}:{datetime.now(timezone.utc):%Y-%m-%d}"
    bus.set_json_if_absent(day_key, account.equity, ttl_seconds=3 * 86400)
    account.day_start_equity = float(bus.get_json(day_key))
    account.timestamp = datetime.now(timezone.utc)
    bus.set_json(settings.key_account_snapshot, account, ttl_seconds=STATE_TTL_SECONDS)

    for pair in settings.forex_pairs:
        try:
            spec = broker.symbol_spec(pair)
            if spec:
                bus.set_json(settings.key_symbol_spec_prefix + pair, spec, ttl_seconds=STATE_TTL_SECONDS)
        except Exception as e:
            log.warning(f"No se pudo publicar la especificación de {pair}: {e}")


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    broker = build_broker()
    if not broker.connect():
        raise SystemExit("No se pudo conectar con el broker. Revisa que MT5 esté abierto y las credenciales del .env.")

    if isinstance(broker, MT5Broker) and broker.is_real_account and not settings.allow_live_trading:
        broker.close()
        raise SystemExit(
            "🛑 La cuenta MT5 conectada es REAL y ALLOW_LIVE_TRADING no está en true. "
            "Opera primero en una cuenta demo; si de verdad quieres operar dinero real, cambia ALLOW_LIVE_TRADING=true en .env."
        )

    log.info(f"Iniciando execution-engine | Modo: {broker.mode.upper()} | Dry-Run: {settings.dry_run}")

    bus = EventBus()
    repo = OrderRepository()
    order_mgr = OrderManager(broker, repo)
    recon = OrderReconciliation(broker, repo, bus=bus)

    timers = {"snapshot": 0.0, "reconcile": 0.0}

    def periodic_tasks():
        now = time.monotonic()
        if now - timers["snapshot"] >= SNAPSHOT_EVERY_SECONDS:
            timers["snapshot"] = now
            try:
                publish_state(broker, bus)
            except Exception:
                log.exception("Error publicando el estado de la cuenta")
        if now - timers["reconcile"] >= RECONCILE_EVERY_SECONDS:
            timers["reconcile"] = now
            try:
                recon.reconcile_orders()
            except Exception:
                log.exception("Error en ciclo de reconciliación")

    try:
        for channel, data in bus.subscribe(
            settings.channel_orders_approved,
            group="execution-engine",
            should_stop=lambda: not running,
            on_idle=periodic_tasks,
        ):
            try:
                order_req = OrderRequest(**data)
                log.info(
                    f"⚡ Orden aprobada #{order_req.order_id}: {order_req.pair} "
                    f"{order_req.direction.value.upper()} | {order_req.volume_lots} lotes"
                )
                execution = order_mgr.execute_order(order_req)
                if execution is not None:
                    bus.publish(settings.channel_orders_executed, execution)
                    # refresca de inmediato el snapshot (posiciones y margen cambiaron)
                    timers["snapshot"] = 0.0
            except Exception as item_err:
                log.exception(f"Error ejecutando orden aprobada: {item_err}")
    finally:
        repo.close()
        broker.close()
        bus.close()
        log.info("Servicio execution-engine finalizado.")


if __name__ == "__main__":
    main()
