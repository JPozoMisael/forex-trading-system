"""
Punto de entrada del servicio monitoring.
Escucha todos los eventos del ecosistema en Redis (señales, órdenes, cierres, alertas de riesgo)
y los despacha como notificaciones en vivo hacia Telegram y consola.
"""
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus

from telegram_alerts import TelegramNotifier
from email_alerts import EmailNotifier
from health_check import HealthChecker, failure_message
from daily_report import due_slot, send_daily_report

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo monitoring...")
    running = False


class AlertState:
    """
    Avisa solo cuando un fallo dura `confirm_checks` chequeos seguidos (un
    redeploy o el reentreno del modelo no generan alertas) y una vez al
    recuperarse.
    """

    def __init__(self, confirm_checks: int = 3):
        self.confirm_checks = confirm_checks
        self.bad_streak = 0
        self.alerted = False

    def update(self, status: str) -> Optional[str]:
        """Devuelve "fallo", "recuperado" o None."""
        if status == "UP":
            self.bad_streak = 0
            if self.alerted:
                self.alerted = False
                return "recuperado"
            return None
        self.bad_streak += 1
        if self.bad_streak >= self.confirm_checks and not self.alerted:
            self.alerted = True
            return "fallo"
        return None


def _maybe_send_report(bus: EventBus, checker: HealthChecker, notifier, email_notifier,
                       now: Optional[datetime] = None, send=None) -> Optional[str]:
    """Envía el reporte si toca un turno que no se envió. Devuelve el turno enviado."""
    now = now or datetime.now(timezone.utc)
    # Como objeto JSON: un string suelto no se puede leer con get_json (por eso se repetía el envío)
    last = (bus.get_json(settings.key_daily_report_sent) or {})
    slot = due_slot(now, last.get("turno") if isinstance(last, dict) else None, settings.report_times_utc)
    if slot is None:
        return None
    # Se marca antes de enviar: si el envío falla no se reintenta cada minuto
    bus.set_json(settings.key_daily_report_sent, {"turno": slot})
    log.info(f"📧 Enviando reporte del turno {slot} UTC...")
    (send or send_daily_report)(email_notifier, notifier, bus, checker, now)
    return slot


def _health_monitor_loop(checker: HealthChecker, notifier: TelegramNotifier, email_notifier: EmailNotifier, bus: EventBus):
    """Hilo periódico: salud del sistema cada 60 s y reporte diario a su hora."""
    log.info("Iniciando monitor periódico de salud...")
    alerts = AlertState()
    while running:
        try:
            report = checker.run_full_check()
            log.info(
                f"❤️ Estado del Sistema: {report['overall_status']} | PG: {report['postgres'].get('status')} | "
                f"Redis: {report['redis'].get('status')} | Broker: {report['broker'].get('status')} | "
                f"Velas: {report['datos'].get('status')} | Modelo: {report['estrategia'].get('status')}"
            )
            if report["overall_status"] != "UP":
                log.warning(f"⚠️ Componentes con fallo:\n{failure_message(report)}")
            event = alerts.update(report["overall_status"])
            if event and settings.enable_system_alerts:
                failing = failure_message(report)
                if event == "fallo":
                    notifier.send_message(f"🚨 *Fallo en el sistema* (más de 3 min)\n{failing.replace('_', ' ')}")
                else:
                    notifier.send_message("✅ *Sistema recuperado*: todos los componentes operativos.")
                email_notifier.send_system_alert(failing, recovered=(event == "recuperado"))
        except Exception as e:
            log.error(f"Error en health check: {e}")

        try:
            if settings.enable_daily_report:
                _maybe_send_report(bus, checker, notifier, email_notifier)
        except Exception:
            log.exception("Error generando el reporte")

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
    # Conexión propia para el hilo: el bucle principal usa `bus` para leer los streams
    health_thread = threading.Thread(target=_health_monitor_loop,
                                     args=(checker, notifier, email_notifier, EventBus()), daemon=True)
    health_thread.start()

    channels = [
        settings.channel_signals_new,
        settings.channel_orders_executed,
        settings.channel_orders_closed,
        settings.channel_risk_alerts,
    ]

    log.info(f"Escuchando canales de eventos para alertas: {channels}")

    try:
        for channel, data in bus.subscribe(*channels, group="monitoring", should_stop=lambda: not running):

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

    finally:
        checker.close()
        bus.close()
        log.info("Servicio monitoring finalizado.")


if __name__ == "__main__":
    main()
