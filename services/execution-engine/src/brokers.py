"""
Adaptadores de broker para el execution-engine.

  - MT5Broker: XM (o cualquier broker MT5), cuenta demo o real.
  - SimulatedBroker: paper trading con los precios reales guardados en la BD.

Ambos exponen la misma interfaz, así el resto del servicio (ejecución,
reconciliación, snapshot de cuenta) no sabe contra cuál está operando.
"""
import uuid
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set

import psycopg2
from psycopg2.extras import RealDictCursor

from shared.config import settings
from shared.fx import get_pip_size, pnl_in_usd, simulated_symbol_spec
from shared.logger import get_logger
from shared.models import AccountSummary, OrderRequest, OrderSide, SymbolSpec

log = get_logger(__name__)


def order_comment(order_id: int) -> str:
    """Comentario que identifica la posición en MT5 (permite recuperarla tras un reinicio)."""
    return f"fx#{order_id}"


class Broker(ABC):
    mode: str = ""

    @abstractmethod
    def connect(self) -> bool: ...

    @abstractmethod
    def account_summary(self) -> Optional[AccountSummary]: ...

    @abstractmethod
    def symbol_spec(self, pair: str) -> Optional[SymbolSpec]: ...

    @abstractmethod
    def market_order(self, req: OrderRequest) -> Dict[str, Any]:
        """Retorna {"ok", "ticket", "price", "sl", "tp", "volume", "error"}."""

    @abstractmethod
    def open_tickets(self) -> Set[str]:
        """Tickets de posiciones abiertas por el sistema. Lanza excepción si no se puede consultar."""

    @abstractmethod
    def closed_result(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Resultado de una posición cerrada {"exit_price","pnl","exit_reason","closed_at"} o None si sigue abierta."""

    @abstractmethod
    def find_position_by_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        """Busca la posición abierta para una orden cuyo envío quedó a medias."""

    def close(self):
        pass


class MT5Broker(Broker):
    mode = "mt5"

    def __init__(self):
        from shared.mt5_client import MT5Client

        self.client = MT5Client()

    def connect(self) -> bool:
        return self.client.connect()

    @property
    def is_real_account(self) -> bool:
        return self.client.is_real_account

    def account_summary(self) -> Optional[AccountSummary]:
        return self.client.get_account_summary()

    def symbol_spec(self, pair: str) -> Optional[SymbolSpec]:
        return self.client.get_symbol_spec(pair)

    def market_order(self, req: OrderRequest) -> Dict[str, Any]:
        if req.entry_price is None or req.stop_loss is None or req.take_profit is None:
            return {"ok": False, "error": "Orden sin entrada/SL/TP"}
        return self.client.market_order(
            pair=req.pair,
            side=req.direction,
            volume_lots=req.volume_lots,
            sl_distance=abs(req.entry_price - req.stop_loss),
            tp_distance=abs(req.take_profit - req.entry_price),
            comment=order_comment(req.order_id),
        )

    def open_tickets(self) -> Set[str]:
        return set(self.client.get_open_positions().keys())

    def closed_result(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self.client.get_closed_position(row["broker_ticket"])

    def find_position_by_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        p = self.client.find_position_by_comment(order_comment(order_id))
        if p is None:
            return None
        return {"ticket": str(p.ticket), "price": p.price_open, "sl": p.sl, "tp": p.tp, "volume": p.volume}

    def close(self):
        self.client.disconnect()


class SimulatedBroker(Broker):
    """
    Paper trading: ejecuta al último cierre de la BD (+ medio spread) y detecta
    SL/TP recorriendo las velas posteriores a la apertura (si en la misma vela
    se tocan ambos, se asume SL: criterio conservador). Cuenta en USD.
    """

    mode = "sim"
    SPREAD_PIPS = 1.0

    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.postgres_dsn
        self._conn = None

    def _cursor(self):
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn, connect_timeout=5)
            self._conn.autocommit = True
        return self._conn.cursor(cursor_factory=RealDictCursor)

    def connect(self) -> bool:
        try:
            with self._cursor() as cur:
                cur.execute("SELECT 1;")
            log.info("Execution Engine en modo SIMULACIÓN / PAPER TRADING (sin riesgo real).")
            return True
        except Exception as e:
            log.error(f"Simulador sin acceso a la BD: {e}")
            return False

    def _last_close(self, pair: str) -> Optional[float]:
        with self._cursor() as cur:
            cur.execute(
                "SELECT close FROM candles WHERE pair = %s AND granularity = %s ORDER BY timestamp DESC LIMIT 1;",
                (pair, settings.candle_granularity),
            )
            r = cur.fetchone()
        return float(r["close"]) if r else None

    def account_summary(self) -> Optional[AccountSummary]:
        with self._cursor() as cur:
            cur.execute("SELECT COALESCE(SUM(pnl), 0) AS pnl FROM orders WHERE status = 'closed' AND execution_mode = 'sim';")
            realized = float(cur.fetchone()["pnl"])
            cur.execute(
                "SELECT pair, direction, units, entry_price FROM orders WHERE status = 'filled' AND execution_mode = 'sim';"
            )
            open_rows = cur.fetchall()

        floating = 0.0
        for r in open_rows:
            price = self._last_close(r["pair"])
            if price is None:
                continue
            diff = price - r["entry_price"] if r["direction"] == OrderSide.BUY.value else r["entry_price"] - price
            floating += pnl_in_usd(r["pair"], diff, r["units"], price) or 0.0

        balance = settings.sim_initial_balance + realized
        return AccountSummary(
            balance=round(balance, 2),
            equity=round(balance + floating, 2),
            unrealized_pnl=round(floating, 2),
            realized_pnl=round(realized, 2),
            open_positions_count=len(open_rows),
            currency="USD",
            source="simulated",
        )

    def symbol_spec(self, pair: str) -> Optional[SymbolSpec]:
        price = self._last_close(pair)
        if price is None:
            return None
        return simulated_symbol_spec(pair, price, spread_pips=self.SPREAD_PIPS)

    def market_order(self, req: OrderRequest) -> Dict[str, Any]:
        price = self._last_close(req.pair)
        if price is None or req.entry_price is None or req.stop_loss is None or req.take_profit is None:
            return {"ok": False, "error": "Sin precio en BD o la orden no trae SL/TP"}
        half_spread = self.SPREAD_PIPS * get_pip_size(req.pair) / 2
        sl_dist = abs(req.entry_price - req.stop_loss)
        tp_dist = abs(req.take_profit - req.entry_price)
        if req.direction == OrderSide.BUY:
            fill = price + half_spread
            sl, tp = fill - sl_dist, fill + tp_dist
        else:
            fill = price - half_spread
            sl, tp = fill + sl_dist, fill - tp_dist
        return {
            "ok": True,
            "ticket": f"SIM_{uuid.uuid4().hex[:8].upper()}",
            "price": round(fill, 5),
            "sl": round(sl, 5),
            "tp": round(tp, 5),
            "volume": req.volume_lots,
        }

    def open_tickets(self) -> Set[str]:
        return set()  # se determina vela a vela en closed_result()

    def closed_result(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._cursor() as cur:
            cur.execute(
                """
                SELECT timestamp, high, low FROM candles
                WHERE pair = %s AND granularity = %s AND timestamp >= %s
                ORDER BY timestamp ASC;
                """,
                (row["pair"], settings.candle_granularity, row["opened_at"]),
            )
            candles = cur.fetchall()

        is_buy = row["direction"] == OrderSide.BUY.value
        sl, tp = row["stop_loss"], row["take_profit"]
        for c in candles:
            if is_buy:
                hit = ("sl", sl) if c["low"] <= sl else ("tp", tp) if c["high"] >= tp else None
            else:
                hit = ("sl", sl) if c["high"] >= sl else ("tp", tp) if c["low"] <= tp else None
            if hit:
                reason, exit_price = hit
                diff = exit_price - row["entry_price"] if is_buy else row["entry_price"] - exit_price
                pnl = pnl_in_usd(row["pair"], diff, row["units"], exit_price)
                return {
                    "exit_price": exit_price,
                    "pnl": round(pnl, 2) if pnl is not None else None,
                    "exit_reason": reason,
                    "closed_at": c["timestamp"],
                }
        return None

    def find_position_by_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        return None

    def close(self):
        if self._conn and not self._conn.closed:
            self._conn.close()
        self._conn = None
