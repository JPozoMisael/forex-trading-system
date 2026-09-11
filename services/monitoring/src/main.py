"""
Punto de entrada del servicio monitoring.
Escucha todos los eventos del ecosistema en Redis (señales, órdenes, cierres, alertas de riesgo)
y los despacha como notificaciones en vivo hacia Telegram y consola.
"""
import signal
import sys
import threading
import time

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus

from telegram_alerts import TelegramNotifier
from email_alerts import EmailNotifier
from health_check import HealthChecker

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo monitoring...")
    running = False


def _health_monitor_loop(checker: HealthChecker, notifier: TelegramNotifier):
    """Hilo periódico para verificación de salud del sistema."""
    log.info("Iniciando monitor periódico de salud...")
    while running:
        try:
            report = checker.run_full_check()
            log.info(f"❤️ Estado del Sistema: {report['overall_status']} | PG: {report['postgres'].get('status')} | Redis: {report['redis'].get('status')}")
            if report["overall_status"] != "UP":
                log.warning(f"⚠️ Alerta de Degradación de Infraestructura: {report}")
        except Exception as e:
            log.error(f"Error en health check: {e}")

        for _ in range(60):  # Chequeo cada 60 segundos
            if not running:
                break
            time.sleep(1)


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info("Iniciando servicio monitoring & Telegram Alerts...")

    bus = EventBus()
    notifier = TelegramNotifier()
    email_notifier = EmailNotifier()
    checker = HealthChecker()

    # Iniciar hilo de salud
    health_thread = threading.Thread(target=_health_monitor_loop, args=(checker, notifier), daemon=True)
    health_thread.start()

    channels = [
        settings.channel_signals_new,
        settings.channel_orders_executed,
        settings.channel_orders_closed,
        settings.channel_risk_alerts,
    ]

    log.info(f"Escuchando canales de eventos para alertas: {channels}")

    try:
        for channel, data in bus.subscribe(*channels):
            if not running:
                break

            if channel == settings.channel_signals_new:
                method = "send_signal_alert"
            elif channel == settings.channel_orders_executed:
                method = "send_order_executed_alert"
            elif channel == settings.channel_orders_closed:
                method = "send_trade_closed_alert"
            elif channel == settings.channel_risk_alerts:
                method = "send_risk_alert"
            else:
                continue

            # Cada canal de notificación se despacha en su propio try/except:
            # un fallo en Telegram (p.ej. `confidence` nulo rompiendo el formateo
            # del mensaje) no debe impedir que el Email para el mismo evento salga,
            # y viceversa.
            for label, target in (("Telegram", notifier), ("Email", email_notifier)):
                try:
                    getattr(target, method)(data)
                except Exception as ev_err:
                    log.error(f"Error despachando alerta {label} para canal '{channel}': {ev_err}")

    except KeyboardInterrupt:
        log.info("Deteniendo monitoring...")
    finally:
        checker.close()
        bus.close()
        log.info("Servicio monitoring finalizado.")


if __name__ == "__main__":
    main()
