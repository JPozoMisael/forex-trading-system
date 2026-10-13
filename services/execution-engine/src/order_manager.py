"""
Gestor de órdenes: ejecuta en el broker las órdenes reservadas por el risk-manager.

Flujo idempotente por orden (fila en la tabla `orders`):
    pending  --claim-->  submitting  --broker OK-->  filled  --SL/TP-->  closed
                                     --broker KO-->  rejected

`claim` es un UPDATE condicional: si el mensaje llega dos veces (reintento de
Redis Streams tras un reinicio) la segunda vez no hace nada, así una orden
jamás se envía dos veces al broker.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import settings
from shared.logger import get_logger
from shared.models import OrderExecution, OrderRequest, OrderStatus

from brokers import Broker

log = get_logger(__name__)


class OrderRepository:
    """Acceso a la tabla `orders`."""

    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.postgres_dsn
        self._conn = None

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn, connect_timeout=5)
            self._conn.autocommit = True
        return self._conn.cursor(cursor_factory=RealDictCursor)

    def _execute(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        try:
            with self._cursor() as cur:
                cur.execute(query, params)
                return cur.fetchall() if cur.description else []
        except Exception:
            self.close()  # fuerza reconexión en la próxima llamada
            raise

    def claim(self, order_id: int, mode: str) -> bool:
        rows = self._execute(
            """
            UPDATE orders SET status = 'submitting', execution_mode = %s, updated_at = now()
            WHERE id = %s AND status = 'pending'
            RETURNING id;
            """,
            (mode, order_id),
        )
        return bool(rows)

    def mark_filled(self, order_id: int, ticket: str, price: float, sl: float, tp: float, lots: float, units: int):
        self._execute(
            """
            UPDATE orders SET status = 'filled', broker_ticket = %s, entry_price = %s, stop_loss = %s,
                   take_profit = %s, volume_lots = %s, units = %s, opened_at = now(), updated_at = now()
            WHERE id = %s;
            """,
            (ticket, price, sl, tp, lots, units, order_id),
        )

    def mark_failed(self, order_id: int, status: OrderStatus, error: str):
        self._execute(
            "UPDATE orders SET status = %s, error = %s, updated_at = now() WHERE id = %s;",
            (status.value, error[:500], order_id),
        )

    def mark_closed(self, order_id: int, exit_price: float, pnl: Optional[float], exit_reason: str, closed_at: datetime):
        self._execute(
            """
            UPDATE orders SET status = 'closed', exit_price = %s, pnl = %s, exit_reason = %s,
                   closed_at = %s, updated_at = now()
            WHERE id = %s AND status = 'filled';
            """,
            (exit_price, pnl, exit_reason, closed_at, order_id),
        )

    def rows_with_status(self, status: str, older_than_seconds: int = 0) -> List[Dict[str, Any]]:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        return self._execute(
            "SELECT * FROM orders WHERE status = %s AND COALESCE(updated_at, created_at) <= %s ORDER BY id;",
            (status, cutoff),
        )

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None


class OrderManager:
    def __init__(self, broker: Broker, repo: OrderRepository):
        self.broker = broker
        self.repo = repo

    def execute_order(self, req: OrderRequest) -> Optional[OrderExecution]:
        """Ejecuta la orden en el broker. Retorna None si ya había sido procesada."""
        if req.order_id is None:
            log.error(f"Orden sin order_id (no reservada por el risk-manager); se ignora: {req}")
            return None

        if not self.repo.claim(req.order_id, self.broker.mode):
            log.info(f"Orden #{req.order_id} ya procesada o cancelada; se ignora el duplicado.")
            return None

        age = (datetime.now(timezone.utc) - req.timestamp).total_seconds()
        if age > settings.order_max_age_seconds:
            return self._fail(req, OrderStatus.CANCELLED, f"Orden expirada ({int(age)}s desde la aprobación)")

        try:
            result = self.broker.market_order(req)
        except Exception:
            # Estado incierto: queda 'submitting' y la reconciliación lo resuelve
            # buscando la posición en el broker.
            log.exception(f"Excepción enviando orden #{req.order_id}; se resolverá en la reconciliación")
            return None

        if not result.get("ok"):
            return self._fail(req, OrderStatus.REJECTED, result.get("error", "error desconocido"))

        lots = float(result.get("volume") or req.volume_lots)
        units = int(round(req.units * lots / req.volume_lots))
        self.repo.mark_filled(req.order_id, result["ticket"], result["price"], result["sl"], result["tp"], lots, units)
        execution = OrderExecution(
            order_id=req.order_id,
            pair=req.pair,
            direction=req.direction,
            units=units,
            volume_lots=lots,
            entry_price=result["price"],
            stop_loss=result["sl"],
            take_profit=result["tp"],
            status=OrderStatus.FILLED,
            broker_ticket=result["ticket"],
            strategy_name=req.strategy_name,
            metadata={"mode": self.broker.mode},
        )
        log.info(
            f"⚡ [{self.broker.mode.upper()}] Orden #{req.order_id} ejecutada: {req.pair} {req.direction.value.upper()} "
            f"{lots} lotes @ {result['price']} | SL={result['sl']} TP={result['tp']} | Ticket={result['ticket']}"
        )
        return execution

    def _fail(self, req: OrderRequest, status: OrderStatus, error: str) -> OrderExecution:
        log.warning(f"Orden #{req.order_id} {status.value}: {error}")
        self.repo.mark_failed(req.order_id, status, error)
        return OrderExecution(
            order_id=req.order_id, pair=req.pair, direction=req.direction, units=req.units,
            volume_lots=req.volume_lots, entry_price=req.entry_price or 0.0, status=status,
            strategy_name=req.strategy_name, error=error,
        )
