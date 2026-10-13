"""
Módulo de Reconciliación y Sincronización de Posiciones.

La fuente de verdad es el BROKER, no la BD. En cada ciclo:
  1. Cancela órdenes 'pending' que nunca llegaron a ejecutarse.
  2. Resuelve órdenes 'submitting' que quedaron a medias (caída durante el envío).
  3. Cancela posiciones abiertas en otro modo (p.ej. al pasar de simulación a MT5).
  4. Detecta posiciones cerradas en el broker (SL, TP, cierre manual, stop out),
     registra el PnL real y emite el evento de trade cerrado.
  5. Avisa de posiciones con el magic del sistema que la BD no conoce.
"""
from datetime import datetime, timezone
from typing import Optional, Set

from shared.bus import EventBus
from shared.config import settings
from shared.fx import get_pip_size
from shared.logger import get_logger
from shared.models import OrderSide, OrderStatus, TradeResult

from brokers import Broker
from order_manager import OrderRepository

log = get_logger(__name__)

SUBMITTING_GRACE_SECONDS = 60


class OrderReconciliation:
    def __init__(self, broker: Broker, repo: OrderRepository, bus: Optional[EventBus] = None):
        self.broker = broker
        self.repo = repo
        self.bus = bus or EventBus()
        self._alerted_orphans: Set[str] = set()

    def _alert(self, alert_type: str, reason: str, pair: str = "N/A"):
        log.warning(f"⚠️ {reason}")
        try:
            self.bus.publish(
                settings.channel_risk_alerts,
                {"type": alert_type, "pair": pair, "strategy": "execution-engine", "reason": reason,
                 "timestamp": datetime.now(timezone.utc).isoformat()},
            )
        except Exception:
            log.exception("No se pudo publicar la alerta")

    def reconcile_orders(self):
        self._expire_pending()
        self._resolve_submitting()

        filled = self.repo.rows_with_status(OrderStatus.FILLED.value)
        current = [r for r in filled if r["execution_mode"] == self.broker.mode]
        for row in filled:
            if row["execution_mode"] != self.broker.mode:
                self.repo.mark_failed(row["id"], OrderStatus.CANCELLED, f"Abierta en modo '{row['execution_mode']}'; el sistema ahora opera en '{self.broker.mode}'")
                self._alert("mode_changed", f"Orden #{row['id']} ({row['pair']}) cancelada por cambio de modo de ejecución", row["pair"])

        open_tickets = self.broker.open_tickets()  # lanza excepción si el broker no responde
        known = {r["broker_ticket"] for r in current}

        for row in current:
            if row["broker_ticket"] in open_tickets:
                continue
            result = self.broker.closed_result(row)
            if result is not None:
                self._close(row, result)

        for ticket in open_tickets - known - self._alerted_orphans:
            self._alerted_orphans.add(ticket)
            self._alert("orphan_position", f"Posición {ticket} con el magic del sistema no está en la BD; revísala en MT5")

    def _expire_pending(self):
        for row in self.repo.rows_with_status(OrderStatus.PENDING.value, older_than_seconds=settings.order_max_age_seconds + 60):
            self.repo.mark_failed(row["id"], OrderStatus.CANCELLED, "Nunca llegó al execution-engine")
            log.warning(f"Orden pendiente #{row['id']} ({row['pair']}) cancelada por antigüedad")

    def _resolve_submitting(self):
        for row in self.repo.rows_with_status(OrderStatus.SUBMITTING.value, older_than_seconds=SUBMITTING_GRACE_SECONDS):
            if row["execution_mode"] != self.broker.mode:
                self.repo.mark_failed(row["id"], OrderStatus.CANCELLED, "Envío interrumpido en otro modo de ejecución")
                continue
            pos = self.broker.find_position_by_order(row["id"])
            if pos:
                units = int(round(row["units"] * pos["volume"] / row["volume_lots"])) if row["volume_lots"] else row["units"]
                self.repo.mark_filled(row["id"], pos["ticket"], pos["price"], pos["sl"], pos["tp"], pos["volume"], units)
                log.info(f"Orden #{row['id']} recuperada: posición {pos['ticket']} encontrada en el broker")
            else:
                self.repo.mark_failed(row["id"], OrderStatus.REJECTED, "Envío interrumpido y sin posición en el broker")
                self._alert(
                    "uncertain_order",
                    f"Orden #{row['id']} ({row['pair']}) quedó a medias y no se encontró abierta; verifica el historial de MT5",
                    row["pair"],
                )

    def _close(self, row: dict, result: dict):
        pair = row["pair"]
        entry = float(row["entry_price"])
        exit_price = float(result["exit_price"])
        is_buy = row["direction"] == OrderSide.BUY.value
        diff = exit_price - entry if is_buy else entry - exit_price
        pips = diff / get_pip_size(pair)
        closed_at = result["closed_at"]
        opened_at = row["opened_at"] or closed_at
        pnl = result["pnl"]

        self.repo.mark_closed(row["id"], exit_price, pnl, result["exit_reason"], closed_at)
        pnl_txt = f"{pnl:+.2f}" if pnl is not None else "n/d"
        log.info(
            f"💰 TRADE CERRADO #{row['id']} ({result['exit_reason'].upper()}): {pair} {row['direction'].upper()} | "
            f"Entrada={entry} -> Salida={exit_price} | PnL={pnl_txt} ({pips:+.1f} pips)"
        )

        risk = row.get("risk_amount") or 0
        trade_result = TradeResult(
            order_id=row["id"],
            pair=pair,
            direction=OrderSide(row["direction"]),
            units=int(row["units"]),
            entry_price=entry,
            exit_price=exit_price,
            pnl=pnl,
            pips=round(pips, 1),
            # ROI expresado como múltiplo del riesgo asumido (R) en %
            roi_pct=round(pnl / risk * 100.0, 1) if pnl is not None and risk else 0.0,
            opened_at=opened_at,
            closed_at=closed_at,
            duration_minutes=round(max(0.0, (closed_at - opened_at).total_seconds() / 60.0), 1),
            strategy_name=row.get("strategy_name") or "desconocida",
            exit_reason=result["exit_reason"],
        )
        self.bus.publish(settings.channel_orders_closed, trade_result)
