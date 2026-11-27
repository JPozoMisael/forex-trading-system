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
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import SIDE_STRATEGIES

from telegram_alerts import TelegramNotifier
from email_alerts import EmailNotifier
from health_check import HealthChecker, failure_message
from daily_report import due_slot, send_daily_report
from trade_report import send_trade_report
from signal_chart import send_signal_report

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


DUPLICATE_POSITION_REASON = "Ya existe una posición activa"


def _position_open_before(pair: str, when: Any) -> bool:
    """¿Hay una posición del modelo en `pair` ya ejecutada (filled) antes de `when`? Ante error de BD: False (se avisa)."""
    import psycopg2

    try:
        conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=3)
        try:
            with conn.cursor() as cur:
                # Las de prueba/práctica no bloquean al modelo: su señal sí se avisa
                cur.execute("SELECT 1 FROM orders WHERE pair = %s AND status = 'filled' AND opened_at < %s "
                            "AND (strategy_name IS NULL OR NOT strategy_name = ANY(%s)) LIMIT 1;",
                            (pair, when, SIDE_STRATEGIES))
                return cur.fetchone() is not None
        finally:
            conn.close()
    except Exception as e:
        log.warning(f"No se pudo consultar posiciones abiertas de {pair}: {e}")
        return False


def is_redundant(channel: str, data: dict, position_open_before) -> bool:
    """
    Avisos que no aportan nada: con una posición ya abierta en el par, el
    modelo puede repetir la señal cada vela y el risk-manager la rechaza por
    duplicada. Se registran en el log pero no se envían por email/Telegram.
    """
    if channel == settings.channel_risk_alerts:
        return str(data.get("reason", "")).startswith(DUPLICATE_POSITION_REASON)
    if channel == settings.channel_signals_new and data.get("pair"):
        return position_open_before(data["pair"], data.get("timestamp") or datetime.now(timezone.utc))
    return False


REPORT_ATTEMPTS = 3        # intentos de armar el reporte si falla antes de enviar nada
REPORT_RETRY_MINUTES = 5   # espera entre esos intentos


def _warn(notifier, email_notifier, text: str, by_email: bool) -> None:
    """Aviso de que el reporte no salió. Por Telegram siempre; por correo solo si el correo no es lo que falla."""
    log.error(text.replace("*", ""))
    for send in ([notifier.send_message] if notifier else []) + (
            [lambda t: email_notifier.send_message("⚠️ Reporte no enviado", t.replace("*", ""))]
            if by_email and email_notifier else []):
        try:
            send(text)
        except Exception:
            log.exception("No se pudo enviar el aviso de reporte fallido")


def _maybe_send_report(bus: EventBus, checker: HealthChecker, notifier, email_notifier,
                       now: Optional[datetime] = None, send=None) -> Optional[str]:
    """
    Envía el reporte si toca un turno que no se terminó. Devuelve el turno si se envió.

    Estado en Redis: {"turno", "intentos", "listo", "siguiente"}. Se marca ANTES de
    cada intento, así un fallo no se repite cada minuto: si el reporte falla al
    armarse, se reintenta cada REPORT_RETRY_MINUTES hasta REPORT_ATTEMPTS veces y
    luego se avisa. Si lo que falla es el correo, send_daily_report ya lo reintentó
    y aquí solo se avisa por Telegram (repetir todo duplicaría el Telegram y la práctica).
    """
    now = now or datetime.now(timezone.utc)
    key = settings.key_daily_report_sent
    # Como objeto JSON: un string suelto no se puede leer con get_json (por eso se repetía el envío)
    state = bus.get_json(key) or {}
    state = state if isinstance(state, dict) else {}
    done = bool(state.get("listo", True))  # estado anterior ({"turno": ...} solo) = terminado
    slot = due_slot(now, state.get("turno") if done else None, settings.report_times)
    if slot is None:
        return None
    tries = 0
    if state.get("turno") == slot and not done:
        tries = int(state.get("intentos", 0))
        nxt = state.get("siguiente")
        if nxt and now < datetime.fromisoformat(nxt):
            return None
    tries += 1
    hora = slot.split(" ")[-1]
    bus.set_json(key, {"turno": slot, "intentos": tries, "listo": tries >= REPORT_ATTEMPTS,
                       "siguiente": (now + timedelta(minutes=REPORT_RETRY_MINUTES)).isoformat()})
    log.info(f"📧 Enviando reporte del turno {slot} (hora local, intento {tries}/{REPORT_ATTEMPTS})...")
    try:
        sent = (send or send_daily_report)(email_notifier, notifier, bus, checker, now)
    except Exception as e:
        log.exception(f"Error generando el reporte del turno {slot}")
        if tries >= REPORT_ATTEMPTS:
            _warn(notifier, email_notifier, f"⚠️ *No se pudo generar el reporte de las {hora}* tras {tries} "
                  f"intentos: {e}. Revisa el log del contenedor (\"Error generando el reporte\").", by_email=True)
        return None
    bus.set_json(key, {"turno": slot, "intentos": tries, "listo": True})
    if sent is False:
        _warn(notifier, email_notifier, f"⚠️ *El correo del reporte de las {hora} no se pudo enviar* (3 intentos). "
              "El reporte sí salió por Telegram. Revisa en el log \"Error enviando correo\": suele ser la "
              "contraseña de aplicación de Gmail o EMAIL\\_RECIPIENT.", by_email=False)
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

            if is_redundant(channel, data, _position_open_before):
                log.info(f"Aviso no enviado (repetido, ya hay posición abierta en {data.get('pair')}): {channel}")
                continue

            # Cada canal de notificación se despacha en su propio try/except:
            # un fallo en Telegram (p.ej. `confidence` nulo rompiendo el formateo
            # del mensaje) no debe impedir que el Email para el mismo evento salga,
            # y viceversa.
            for label, target in (("Telegram", notifier), ("Email", email_notifier)):
                try:
                    if label == "Email" and channel == settings.channel_orders_closed:
                        send_trade_report(email_notifier, data)  # con gráfico de la operación
                    elif label == "Email" and channel == settings.channel_signals_new:
                        send_signal_report(email_notifier, data)  # con soportes, resistencias y mechas
                    else:
                        getattr(target, method)(data)
                except Exception as ev_err:
                    log.error(f"Error despachando alerta {label} para canal '{channel}': {ev_err}")

    finally:
        checker.close()
        bus.close()
        log.info("Servicio monitoring finalizado.")


if __name__ == "__main__":
    main()
