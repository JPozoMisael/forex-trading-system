"""
Módulo de Reconciliación y Sincronización de Posiciones.
Compara periódicamente el estado de las órdenes en la BD contra el broker real (o el simulador),
detecta cierres de operaciones por Take Profit o Stop Loss, calcula el PnL y emite eventos de trade cerrado.
"""
from datetime import datetime, timezone
from typing import List, Optional
import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import settings
from shared.logger import get_logger
from shared.models import OrderStatus, TradeResult, OrderSide
from shared.bus import EventBus

log = get_logger(__name__)


class OrderReconciliation:
    def __init__(self, dsn: Optional[str] = None, bus: Optional[EventBus] = None):
        self.dsn = dsn or settings.postgres_dsn
        self.bus = bus or EventBus()
        self._conn: Optional[psycopg2.extensions.connection] = None

    def _get_connection(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = True
        return self._conn

    def reconcile_orders(self):
        """
        Ejecuta un ciclo de reconciliación sobre todas las órdenes abiertas registradas.
        """
        try:
            conn = self._get_connection()
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, pair, direction, units, entry_price, stop_loss, take_profit,
                           status, oanda_order_id, opened_at
                    FROM orders
                    WHERE status IN ('filled', 'open', 'pending')
                    ORDER BY id ASC;
                    """
                )
                open_orders = cur.fetchall()

            if not open_orders:
                return

            log.debug(f"Reconciliando {len(open_orders)} órdenes abiertas...")

            for order in open_orders:
                self._check_order_closure(order)

        except Exception as e:
            log.warning(f"Error en ciclo de reconciliación: {e}")

    def _check_order_closure(self, order: dict):
        """
        Verifica si la orden alcanzó Stop Loss o Take Profit consultando las últimas cotizaciones.
        """
        pair = order["pair"]
        direction = order["direction"]
        entry = float(order["entry_price"])
        sl = float(order["stop_loss"]) if order["stop_loss"] else None
        tp = float(order["take_profit"]) if order["take_profit"] else None
        units = int(order["units"])
        opened_at = order["opened_at"]

        # Obtenemos el último precio de cierre registrado para el par
        current_price = self._get_latest_price(pair)
        if not current_price:
            return

        should_close = False
        exit_reason = "normal"
        exit_price = current_price

        if direction == OrderSide.BUY.value:
            if tp and current_price >= tp:
                should_close = True
                exit_reason = "tp"
                exit_price = tp
            elif sl and current_price <= sl:
                should_close = True
                exit_reason = "sl"
                exit_price = sl
        elif direction == OrderSide.SELL.value:
            if tp and current_price <= tp:
                should_close = True
                exit_reason = "tp"
                exit_price = tp
            elif sl and current_price >= sl:
                should_close = True
                exit_reason = "sl"
                exit_price = sl

        if should_close:
            self._close_order(order, exit_price, exit_reason)

    def _get_latest_price(self, pair: str) -> Optional[float]:
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT close FROM candles WHERE pair = %s ORDER BY timestamp DESC LIMIT 1;",
                    (pair,),
                )
                res = cur.fetchone()
                return float(res[0]) if res else None
        except Exception:
            return None

    def _close_order(self, order: dict, exit_price: float, exit_reason: str):
        order_id = order["id"]
        pair = order["pair"]
        direction = order["direction"]
        units = int(order["units"])
        entry_price = float(order["entry_price"])
        opened_at = order["opened_at"]
        closed_at = datetime.now(timezone.utc)

        # Cálculo de PnL y pips
        pip_size = 0.01 if "JPY" in pair else 0.0001
        if direction == OrderSide.BUY.value:
            pnl_diff = exit_price - entry_price
            pips = (exit_price - entry_price) / pip_size
        else:
            pnl_diff = entry_price - exit_price
            pips = (entry_price - exit_price) / pip_size

        pnl_usd = round(pnl_diff * units, 2)
        roi_pct = round((pnl_diff / entry_price) * 100.0, 2)
        duration_mins = max(1.0, (closed_at - opened_at).total_seconds() / 60.0)

        # Actualizar en BD
        try:
            conn = self._get_connection()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orders
                    SET status = 'closed',
                        closed_at = %s,
                        pnl = %s
                    WHERE id = %s;
                    """,
                    (closed_at, pnl_usd, order_id),
                )

            log.info(
                f"💰 TRADE CERRADO #{order_id} ({exit_reason.upper()}): {pair} {direction.upper()} | "
                f"Entrada={entry_price} -> Salida={exit_price} | PnL=${pnl_usd:+.2f} ({pips:+.1f} pips)"
            )

            # Notificar vía Redis
            trade_result = TradeResult(
                pair=pair,
                direction=OrderSide(direction),
                units=units,
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl_usd,
                pips=round(pips, 1),
                roi_pct=roi_pct,
                opened_at=opened_at,
                closed_at=closed_at,
                duration_minutes=round(duration_mins, 1),
                strategy_name="system",
                exit_reason=exit_reason,
            )

            self.bus.publish(settings.channel_orders_closed, trade_result)

        except Exception as e:
            log.error(f"Error cerrando orden #{order_id} en BD: {e}")

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self.bus.close()
