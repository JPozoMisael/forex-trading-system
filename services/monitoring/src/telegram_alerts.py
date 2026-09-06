"""
Cliente de notificaciones y alertas instantáneas para Telegram.
Formatea mensajes elegantes en Markdown con emojis para señales, ejecuciones, cierres y alertas de riesgo.
"""
from typing import Optional, Dict, Any
import requests

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)


class TelegramNotifier:
    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ):
        self.bot_token = bot_token or settings.telegram_bot_token
        self.chat_id = chat_id or settings.telegram_chat_id
        self.is_configured = bool(self.bot_token and self.chat_id and self.bot_token != "tu_token_aqui")

        if self.is_configured:
            log.info("Notificador Telegram inicializado y habilitado.")
        else:
            log.warning("TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados. Las alertas se registrarán en los logs del contenedor.")

    def send_message(self, text: str) -> bool:
        """Envía un mensaje de texto formateado en Markdown a Telegram."""
        if not self.is_configured:
            log.info(f"\n[TELEGRAM SIMULADO]\n{text}\n" + "-" * 40)
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            res = requests.post(url, json=payload, timeout=8)
            if res.status_code == 200:
                log.debug("Mensaje enviado a Telegram exitosamente.")
                return True
            else:
                log.error(f"Error enviando mensaje a Telegram (HTTP {res.status_code}): {res.text}")
                return False
        except Exception as e:
            log.error(f"Excepción al conectar con la API de Telegram: {e}")
            return False

    def send_signal_alert(self, sig: Dict[str, Any]):
        """Notificación cuando el Strategy Engine genera una señal."""
        if not settings.enable_telegram_signals:
            return

        pair = sig.get("pair", "UNKNOWN")
        direction = str(sig.get("direction", "")).upper()
        strategy = sig.get("strategy_name", "N/A")
        price = sig.get("entry_price", "N/A")
        sl = sig.get("stop_loss", "N/A")
        tp = sig.get("take_profit", "N/A")
        conf = sig.get("confidence", 1.0) * 100

        emoji_dir = "🟢 COMPRA (BUY)" if "BUY" in direction else "🔴 VENTA (SELL)"

        text = (
            f"🎯 *NUEVA SEÑAL DETECTADA*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Par:* `{pair}`\n"
            f"⚡ *Acción:* *{emoji_dir}*\n"
            f"🧠 *Estrategia:* `{strategy}` (Confianza: {conf:.0f}%)\n"
            f"💵 *Precio Estimado:* `{price}`\n"
            f"🛑 *Stop Loss:* `{sl}`\n"
            f"🎯 *Take Profit:* `{tp}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏳ _Enviado a Risk Manager para validación..._"
        )
        self.send_message(text)

    def send_order_executed_alert(self, order: Dict[str, Any]):
        """Notificación cuando el Execution Engine envía la orden al broker."""
        if not settings.enable_telegram_orders:
            return

        pair = order.get("pair", "UNKNOWN")
        direction = str(order.get("direction", "")).upper()
        units = order.get("units", 0)
        entry = order.get("entry_price", 0.0)
        ticket = order.get("oanda_order_id", "SIMULATED")
        sl = order.get("stop_loss", "N/A")
        tp = order.get("take_profit", "N/A")
        mode = "🟢 LIVE" if settings.oanda_environment == "live" and not settings.dry_run else "🟡 DEMO / SIM"

        text = (
            f"⚡ *ORDEN EJECUTADA EN BROKER*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🌐 *Ambiente:* `{mode}`\n"
            f"📊 *Par:* `{pair}` | *{direction}*\n"
            f"📦 *Unidades:* `{units:,}`\n"
            f"💰 *Precio Entrada:* `{entry}`\n"
            f"🛑 *Stop Loss:* `{sl}`\n"
            f"🎯 *Take Profit:* `{tp}`\n"
            f"🎫 *Ticket ID:* `{ticket}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ _Operación asegurada con SL y TP activos_"
        )
        self.send_message(text)

    def send_trade_closed_alert(self, trade: Dict[str, Any]):
        """Notificación cuando una posición se cierra con su resultado financiero."""
        if not settings.enable_telegram_trades:
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
            status_emoji = "🎉 *TRADE GANADOR (PROFIT)*"
            pnl_str = f"+${pnl:.2f} USD"
            pip_str = f"+{pips:.1f} pips"
        else:
            status_emoji = "🛑 *TRADE CERRADO (LOSS)*"
            pnl_str = f"-${abs(pnl):.2f} USD"
            pip_str = f"{pips:.1f} pips"

        text = (
            f"{status_emoji}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Par:* `{pair}` ({direction})\n"
            f"🏁 *Motivo Cierre:* `{reason}`\n"
            f"💵 *PnL Realizado:* *{pnl_str}* ({pip_str})\n"
            f"📈 *Retorno:* `{roi:+.2f}%`\n"
            f"📥 *Entrada:* `{entry}` ➔ 📤 *Salida:* `{exit_p}`\n"
            f"⏱️ *Duración:* `{duration:.1f} min`\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(text)

    def send_risk_alert(self, alert: Dict[str, Any]):
        """Notificación de alerta o violación de riesgo."""
        reason = alert.get("reason", "Violación de regla")
        pair = alert.get("pair", "N/A")
        strategy = alert.get("strategy", "N/A")

        text = (
            f"⚠️ *ALERTA DE GESTIÓN DE RIESGO*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ *Señal Bloqueada:* `{pair}` ({strategy})\n"
            f"🚫 *Motivo:* `{reason}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔒 _El capital se mantiene protegido._"
        )
        self.send_message(text)
