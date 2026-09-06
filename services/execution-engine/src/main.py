"""
Punto de entrada del servicio execution-engine.
Recibe órdenes aprobadas desde Redis, las ejecuta en OANDA o en el simulador,
y ejecuta un hilo periódico de reconciliación para monitorear órdenes abiertas.
"""
import signal
import sys
import threading
import time

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import OrderRequest, OrderExecution

from order_manager import OrderManager
from reconciliation import OrderReconciliation

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo execution-engine...")
    running = False


def _reconciliation_loop(recon: OrderReconciliation):
    """Hilo de fondo para reconciliación periódica de órdenes abiertas."""
    log.info("Iniciando hilo de reconciliación de órdenes...")
    while running:
        try:
            recon.reconcile_orders()
        except Exception as e:
            log.warning(f"Error en bucle de reconciliación: {e}")

        for _ in range(10):  # Revisar cada 10 segundos
            if not running:
                break
            time.sleep(1)


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info(
        f"Iniciando execution-engine | Broker: OANDA ({settings.oanda_environment}) | "
        f"Modo Dry-Run: {settings.dry_run}"
    )

    bus = EventBus()
    order_mgr = OrderManager()
    recon = OrderReconciliation(bus=bus)

    # Iniciar hilo de reconciliación
    recon_thread = threading.Thread(target=_reconciliation_loop, args=(recon,), daemon=True)
    recon_thread.start()

    try:
        for channel, data in bus.subscribe(settings.channel_orders_approved):
            if not running:
                break

            try:
                order_req = OrderRequest(**data)
                log.info(
                    f"⚡ Recibida orden aprobada para ejecución: {order_req.pair} "
                    f"{order_req.direction.value.upper()} | {order_req.units} unidades"
                )

                # Ejecutar la orden en broker/simulador
                execution = order_mgr.execute_order(order_req)

                # Publicar resultado de ejecución
                bus.publish(settings.channel_orders_executed, execution)
                log.info(
                    f"🚀 Orden despachada y publicada: #{execution.order_id} "
                    f"Status={execution.status.value} Ticket={execution.oanda_order_id}"
                )

            except Exception as item_err:
                log.exception(f"Error ejecutando orden aprobada: {item_err}")

    except KeyboardInterrupt:
        log.info("Deteniendo execution-engine...")
    finally:
        order_mgr.close()
        recon.close()
        bus.close()
        log.info("Servicio execution-engine finalizado.")


if __name__ == "__main__":
    main()
