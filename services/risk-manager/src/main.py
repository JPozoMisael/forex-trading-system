"""
Punto de entrada del servicio risk-manager.
Escucha señales nuevas en Redis, aplica todas las reglas de gestión de riesgo,
dimensiona el tamaño exacto de la orden y publica órdenes aprobadas hacia el execution-engine.
"""
import os
import signal
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.models import Signal, OrderRequest, OrderSide, SignalDirection

from rules import RiskRulesEngine

log = get_logger(__name__)

running = True


def _handle_shutdown(signum, frame):
    global running
    log.info("Señal de parada recibida. Deteniendo risk-manager...")
    running = False


def main():
    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    log.info(
        f"Iniciando risk-manager | Riesgo por trade: {settings.risk_per_trade_pct}% | "
        f"Max Posiciones: {settings.max_open_positions} | Max Drawdown Diario: {settings.max_daily_drawdown_pct}%"
    )

    bus = EventBus()
    rules_engine = RiskRulesEngine(bus=bus)

    def reject(sig: Signal, reason: str):
        log.warning(f"❌ Señal RECHAZADA por Riesgo: {sig.pair} ({sig.strategy_name}) -> Motivo: {reason}")
        bus.publish(
            settings.channel_risk_alerts,
            {
                "type": "signal_rejected",
                "pair": sig.pair,
                "strategy": sig.strategy_name,
                "reason": reason,
                "timestamp": sig.timestamp.isoformat(),
            },
        )

    try:
        # Un solo consumidor que procesa en orden: evaluar + reservar es atómico
        # respecto a la siguiente señal.
        for channel, data in bus.subscribe(settings.channel_signals_new, group="risk-manager", should_stop=lambda: not running):
            try:
                sig = Signal(**data)
                log.info(f"📥 Evaluando señal recibida de [{sig.strategy_name}]: {sig.pair} {sig.direction.value.upper()} @ {sig.entry_price}")

                assessment = rules_engine.assess(sig)
                if not assessment.approved:
                    reject(sig, assessment.reason)
                    continue

                order_dir = OrderSide.BUY if sig.direction == SignalDirection.BUY else OrderSide.SELL
                try:
                    order_id = rules_engine.reserve_order(sig, assessment, order_dir)
                except Exception as db_err:
                    reject(sig, f"No se pudo reservar la orden en BD: {db_err}")
                    continue

                order_req = OrderRequest(
                    order_id=order_id,
                    pair=sig.pair,
                    direction=order_dir,
                    units=assessment.recommended_units,
                    volume_lots=assessment.recommended_lots,
                    entry_price=sig.entry_price,
                    stop_loss=assessment.stop_loss,
                    take_profit=assessment.take_profit,
                    strategy_name=sig.strategy_name,
                    signal_timestamp=sig.timestamp,
                    metadata={
                        "risk_assessment": assessment.model_dump(),
                        "signal_metadata": sig.metadata,
                    },
                )

                bus.publish(settings.channel_orders_approved, order_req)
                log.info(
                    f"✅ Orden APROBADA #{order_id}: {order_req.pair} {order_req.direction.value.upper()} | "
                    f"{order_req.volume_lots} lotes | Riesgo {assessment.risk_amount_usd} ({assessment.risk_percent}%) | "
                    f"SL={order_req.stop_loss} | TP={order_req.take_profit}"
                )

            except Exception as item_err:
                log.exception(f"Error procesando mensaje de señal: {item_err}")

    finally:
        rules_engine.close()
        bus.close()
        log.info("Servicio risk-manager finalizado.")


if __name__ == "__main__":
    main()
