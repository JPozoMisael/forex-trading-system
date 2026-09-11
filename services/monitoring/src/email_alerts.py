"""
Cliente de notificaciones por correo (SMTP, ej. Gmail con "contraseña de aplicación").
Espejo de telegram_alerts.py: mismos eventos, mismo criterio de qué informar,
pero como email en texto plano para quien prefiera revisar su bandeja de entrada.
"""
import smtplib
from email.mime.text import MIMEText
from typing import Optional, Dict, Any

from shared.config import settings
from shared.logger import get_logger

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
        self.recipient = recipient or settings.email_recipient or self.sender
        self.smtp_host = settings.email_smtp_host
        self.smtp_port = settings.email_smtp_port
        self.is_configured = bool(self.sender and self.app_password and self.recipient)

        if self.is_configured:
            log.info(f"Notificador Email inicializado y habilitado (destino: {self.recipient}).")
        else:
            log.warning("EMAIL_SENDER, EMAIL_APP_PASSWORD o EMAIL_RECIPIENT no configurados. Las alertas se registrarán en los logs del contenedor.")

    def send_message(self, subject: str, body: str) -> bool:
        """Envía un correo de texto plano vía SMTP."""
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
                server.sendmail(self.sender, [self.recipient], msg.as_string())
            log.debug(f"Correo '{subject}' enviado exitosamente.")
            return True
        except Exception as e:
            log.error(f"Error enviando correo '{subject}': {e}")
            return False

    def send_signal_alert(self, sig: Dict[str, Any]):
        """Notificación cuando el Strategy Engine genera una señal."""
        if not settings.enable_email_signals:
            return

        pair = sig.get("pair", "UNKNOWN")
        direction = str(sig.get("direction", "")).upper()
        strategy = sig.get("strategy_name", "N/A")
        price = sig.get("entry_price", "N/A")
        sl = sig.get("stop_loss", "N/A")
        tp = sig.get("take_profit", "N/A")
        conf = (sig.get("confidence") if sig.get("confidence") is not None else 1.0) * 100

        subject = f"[Señal] {pair} {direction} - {strategy}"
        body = (
            f"NUEVA SEÑAL DETECTADA\n"
            f"----------------------------------------\n"
            f"Par: {pair}\n"
            f"Acción: {direction}\n"
            f"Estrategia: {strategy} (Confianza: {conf:.0f}%)\n"
            f"Precio Estimado: {price}\n"
            f"Stop Loss: {sl}\n"
            f"Take Profit: {tp}\n"
            f"----------------------------------------\n"
            f"Enviado a Risk Manager para validación..."
        )
        self.send_message(subject, body)

    def send_order_executed_alert(self, order: Dict[str, Any]):
        """Notificación cuando el Execution Engine envía la orden al broker."""
        if not settings.enable_email_orders:
            return

        pair = order.get("pair", "UNKNOWN")
        direction = str(order.get("direction", "")).upper()
        units = order.get("units", 0)
        entry = order.get("entry_price", 0.0)
        ticket = order.get("oanda_order_id", "SIMULATED")
        sl = order.get("stop_loss", "N/A")
        tp = order.get("take_profit", "N/A")
        mode = "LIVE" if settings.oanda_environment == "live" and not settings.dry_run else "DEMO / SIM"

        subject = f"[Orden Ejecutada] {pair} {direction} - {mode}"
        body = (
            f"ORDEN EJECUTADA EN BROKER\n"
            f"----------------------------------------\n"
            f"Ambiente: {mode}\n"
            f"Par: {pair} | {direction}\n"
            f"Unidades: {units:,}\n"
            f"Precio Entrada: {entry}\n"
            f"Stop Loss: {sl}\n"
            f"Take Profit: {tp}\n"
            f"Ticket ID: {ticket}\n"
            f"----------------------------------------\n"
            f"Operación asegurada con SL y TP activos."
        )
        self.send_message(subject, body)

    def send_trade_closed_alert(self, trade: Dict[str, Any]):
        """Notificación cuando una posición se cierra con su resultado financiero."""
        if not settings.enable_email_trades:
            return

        pair = trade.get("pair", "UNKNOWN")
        direction = str(trade.get("direction", "")).upper()
        pnl = float(trade.get("pnl", 0.0))
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
            f"Retorno: {roi:+.2f}%\n"
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
