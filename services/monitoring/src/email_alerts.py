"""
Cliente de notificaciones por correo (SMTP, ej. Gmail con "contraseña de aplicación").
Espejo de telegram_alerts.py: mismos eventos, mismo criterio de qué informar,
pero como email en texto plano para quien prefiera revisar su bandeja de entrada.
"""
import smtplib
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

from shared.config import settings
from shared.logger import get_logger
from shared.timefmt import fmt_local, zona

log = get_logger(__name__)


class EmailNotifier:
    def __init__(
        self,
        sender: Optional[str] = None,
        app_password: Optional[str] = None,
        recipient: Optional[str] = None,
    ):
        self.sender = sender or settings.email_sender
        self.app_password = app_password or settings.email_app_password
        # Uno o varios destinatarios separados por coma: "a@x.com, b@y.com"
        raw = recipient or settings.email_recipient or self.sender or ""
        self.recipients = [r.strip() for r in raw.split(",") if r.strip()]
        self.recipient = ", ".join(self.recipients)
        self.smtp_host = settings.email_smtp_host
        self.smtp_port = settings.email_smtp_port
        self.is_configured = bool(self.sender and self.app_password and self.recipients)

        if self.is_configured:
            log.info(f"Notificador Email inicializado y habilitado (destino: {self.recipient}).")
        else:
            log.warning("EMAIL_SENDER, EMAIL_APP_PASSWORD o EMAIL_RECIPIENT no configurados. Las alertas se registrarán en los logs del contenedor.")

    def send_message(self, subject: str, body: str) -> bool:
        """Envía un correo de texto plano vía SMTP."""
        # Hora local del usuario al pie de cada aviso (el sistema trabaja en UTC)
        body = body + f"\n\nEnviado el {fmt_local(datetime.now(timezone.utc))} ({zona()})."
        if not self.is_configured:
            log.info(f"\n[EMAIL SIMULADO] {subject}\n{body}\n" + "-" * 40)
            return True

        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                server.starttls()
                server.login(self.sender, self.app_password)
                server.sendmail(self.sender, self.recipients, msg.as_string())
            log.debug(f"Correo '{subject}' enviado exitosamente.")
            return True
        except Exception as e:
            log.error(f"Error enviando correo '{subject}': {e}")
            return False

    def send_html(self, subject: str, html: str, text: str, images: Optional[Dict[str, bytes]] = None) -> bool:
        """Correo HTML con versión en texto plano e imágenes PNG incrustadas (en el HTML: src="cid:<nombre>")."""
        if not self.is_configured:
            log.info(f"\n[EMAIL SIMULADO] {subject}\n{text}\n" + "-" * 40)
            return True

        msg = MIMEMultipart("related")
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.recipient
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(text, "plain", "utf-8"))
        alt.attach(MIMEText(html, "html", "utf-8"))
        msg.attach(alt)
        for name, data in (images or {}).items():
            img = MIMEImage(data, "png")
            img.add_header("Content-ID", f"<{name}>")
            img.add_header("Content-Disposition", "inline", filename=f"{name}.png")
            msg.attach(img)

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20) as server:
                server.starttls()
                server.login(self.sender, self.app_password)
                server.sendmail(self.sender, self.recipients, msg.as_string())
            log.info(f"Correo '{subject}' enviado a {self.recipient}.")
            return True
        except Exception as e:
            log.error(f"Error enviando correo '{subject}': {e}")
            return False

    def send_system_alert(self, failing: str, recovered: bool = False):
        """Aviso de fallo o recuperación de componentes del sistema."""
        if not settings.enable_system_alerts:
            return
        if recovered:
            self.send_message("[Sistema recuperado] Todos los componentes funcionan",
                              "El sistema volvió a funcionar con normalidad.")
        else:
            self.send_message("[ALERTA] Fallo en el sistema de trading",
                              "Uno o más componentes fallan desde hace al menos 3 minutos:\n\n"
                              f"{failing}\n\nRevisa los logs en Dokploy.")

    def send_signal_alert(self, sig: Dict[str, Any]):
        """Notificación cuando el Strategy Engine genera una señal."""
        if not settings.enable_email_signals:
            return

        subject, body = signal_message(sig)
        self.send_message(subject, body)

    def send_order_executed_alert(self, order: Dict[str, Any]):
        """Notificación cuando el Execution Engine envía la orden al broker."""
        if not settings.enable_email_orders:
            return

        subject, body = order_executed_message(order)
        self.send_message(subject, body)

    def send_trade_closed_alert(self, trade: Dict[str, Any]):
        """Notificación cuando una posición se cierra con su resultado financiero."""
        if not settings.enable_email_trades:
            return

        pair = trade.get("pair", "UNKNOWN")
        direction = str(trade.get("direction", "")).upper()
        pnl = float(trade.get("pnl") or 0.0)
        pips = float(trade.get("pips", 0.0))
        roi = float(trade.get("roi_pct", 0.0))
        entry = trade.get("entry_price", 0.0)
        exit_p = trade.get("exit_price", 0.0)
        duration = trade.get("duration_minutes", 0.0)
        reason = str(trade.get("exit_reason", "normal")).upper()

        if pnl >= 0:
            status = "TRADE GANADOR (PROFIT)"
            pnl_str = f"+${pnl:.2f} USD"
            pip_str = f"+{pips:.1f} pips"
        else:
            status = "TRADE CERRADO (LOSS)"
            pnl_str = f"-${abs(pnl):.2f} USD"
            pip_str = f"{pips:.1f} pips"

        subject = f"[{status}] {pair} {pnl_str}"
        body = (
            f"{status}\n"
            f"----------------------------------------\n"
            f"Par: {pair} ({direction})\n"
            f"Motivo Cierre: {reason}\n"
            f"PnL Realizado: {pnl_str} ({pip_str})\n"
            f"Resultado / Riesgo: {roi:+.1f}% del riesgo asumido\n"
            f"Entrada: {entry} -> Salida: {exit_p}\n"
            f"Duración: {duration:.1f} min\n"
            f"----------------------------------------"
        )
        self.send_message(subject, body)

    def send_risk_alert(self, alert: Dict[str, Any]):
        """Notificación de alerta o violación de riesgo."""
        reason = alert.get("reason", "Violación de regla")
        pair = alert.get("pair", "N/A")
        strategy = alert.get("strategy", "N/A")

        subject = f"[Alerta de Riesgo] {pair} bloqueada"
        body = (
            f"ALERTA DE GESTIÓN DE RIESGO\n"
            f"----------------------------------------\n"
            f"Señal Bloqueada: {pair} ({strategy})\n"
            f"Motivo: {reason}\n"
            f"----------------------------------------\n"
            f"El capital se mantiene protegido."
        )
        self.send_message(subject, body)


_ORDER_STATUS_ES = {"FILLED": "ABIERTA", "REJECTED": "RECHAZADA", "CANCELLED": "CANCELADA"}
_SIDE_ES = {"BUY": "COMPRA", "SELL": "VENTA"}


def order_executed_message(order: Dict[str, Any]):
    """(asunto, cuerpo) del aviso de orden ejecutada, en palabras y con los precios redondeados."""
    from charts import digits, move_text

    pair = order.get("pair", "UNKNOWN")
    side = _SIDE_ES.get(str(order.get("direction", "")).upper(), str(order.get("direction", "")).upper())
    raw_status = str(order.get("status", "filled")).upper()
    status = _ORDER_STATUS_ES.get(raw_status, raw_status)
    strategy = order.get("strategy_name") or ""
    kind = " · práctica" if strategy == "practica" else (" · prueba" if strategy == "prueba" else "")
    mode = "simulación" if settings.dry_run else f"MT5 {settings.mt5_server}"
    d = digits(pair)
    units = order.get("units") or 0
    lots = order.get("volume_lots")
    size = f"{units:,} onzas" if pair.upper().startswith("XAU") else f"{units:,} unidades"

    def px(key):
        v = order.get(key)
        return f"{float(v):.{d}f}" if isinstance(v, (int, float)) else "-"

    def dist(key):
        v, e = order.get(key), order.get("entry_price")
        return f" ({move_text(pair, float(v) - float(e))} de la entrada)" if isinstance(v, (int, float)) and e else ""

    subject = f"[Orden {status}] {pair} {side}{kind} · {mode}"
    opened = raw_status == "FILLED"
    head = (f"ORDEN {status}: la operación ya está en curso. Te llega otro correo cuando se cierre "
            f"(en el stop o en el objetivo)." if opened else f"ORDEN {status}: la operación NO se abrió.")
    body = (
        f"{head}\n"
        f"----------------------------------------\n"
        f"Ambiente: {mode}\n"
        f"Instrumento: {pair} | {side}{kind}\n"
        f"Tamaño: {lots} lotes ({size})\n"
        f"Entrada: {px('entry_price')}\n"
        f"Stop Loss: {px('stop_loss')}{dist('stop_loss')}\n"
        f"Take Profit: {px('take_profit')}{dist('take_profit')}\n"
        f"Ticket: {order.get('broker_ticket') or '-'}\n"
        + (f"Motivo: {order['error']}\n" if order.get("error") else "")
        + "----------------------------------------\n"
        + ("Protegida con Stop Loss y Take Profit desde que se abrió." if opened else "")
    )
    return subject, body


def why_lines(factores: Any) -> List[str]:
    """Los datos que más movieron la probabilidad, en palabras: '↑ en las últimas 24 horas el precio bajó 0.40 %'."""
    from daily_report import _factor_text

    lines, seen = [], set()
    for f in factores or []:
        try:
            text = (f"{f['dato']}: sin dato todavía" if f.get("valor") is None
                    else _factor_text(f["dato"], float(f["valor"]), settings.granularity_minutes))
            up = float(f["aporte"]) > 0
        except (KeyError, TypeError, ValueError):
            continue
        if text in seen:  # hora_sin y hora_cos son el mismo dato para el usuario
            continue
        seen.add(text)
        lines.append(f"{'↑ a favor' if up else '↓ en contra'}: {text}")
    return lines


def signal_message(sig: Dict[str, Any]):
    """(asunto, cuerpo) del aviso de señal: qué operar, niveles redondeados y por qué lo cree el modelo."""
    from charts import digits, move_text

    pair = sig.get("pair", "UNKNOWN")
    side = _SIDE_ES.get(str(sig.get("direction", "")).upper(), str(sig.get("direction", "")).upper())
    strategy = sig.get("strategy_name") or ""
    kind = {"practica": " · práctica", "prueba": " · prueba"}.get(strategy, "")
    conf = sig.get("confidence")
    meta = sig.get("metadata") or {}
    umbral = meta.get("umbral")
    d = digits(pair)

    def px(key):
        v = sig.get(key)
        return f"{float(v):.{d}f}" if isinstance(v, (int, float)) else "-"

    def dist(key):
        v, e = sig.get(key), sig.get("entry_price")
        return f" ({move_text(pair, float(v) - float(e))})" if isinstance(v, (int, float)) and e else ""

    conf_txt = f"{float(conf) * 100:.0f} %" if isinstance(conf, (int, float)) else "-"
    subject = f"[Señal] {pair} {side}{kind} · {conf_txt}"
    lines = [
        f"SEÑAL: {side} {pair}{kind}",
        "----------------------------------------",
        f"Entrada aprox.: {px('entry_price')}",
        f"Stop Loss: {px('stop_loss')}{dist('stop_loss')}",
        f"Take Profit: {px('take_profit')}{dist('take_profit')}",
        "----------------------------------------",
    ]
    if strategy == "prueba":
        lines.append("POR QUÉ: es una operación de prueba enviada a mano para revisar el circuito; no es un análisis.")
    else:
        lines.append(f"POR QUÉ: confianza {conf_txt}. En 15 años de historia, de las situaciones parecidas a esta, "
                     f"~{conf_txt} tocaron el objetivo antes que el stop.")
        if strategy == "practica" and isinstance(umbral, (int, float)):
            lines.append(f"No llega al {float(umbral) * 100:.0f} % que exige el modelo: es la mejor del momento, "
                         f"se abre como práctica para medir.")
        why = why_lines(meta.get("por_que"))
        if why:
            lines.append("Lo que más pesó:")
            lines += [f"  {w}" for w in why]
        lines.append("Lo que el modelo NO mira: noticias, soportes y resistencias dibujados ni otros mercados; "
                     "solo los datos de las últimas velas.")
    lines += ["----------------------------------------", "Enviada al gestor de riesgo para validar el lote."]
    return subject, "\n".join(lines)
