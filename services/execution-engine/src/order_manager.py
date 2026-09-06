"""
Gestor de órdenes y ejecutor de mercado para OANDA v20.
Soporta órdenes Bracket (Market + Stop Loss + Take Profit) y modo Simulación/Paper Trading.
"""
from datetime import datetime, timezone
import random
import uuid
from typing import Optional

try:
    import psycopg2
except ImportError:
    psycopg2 = None

try:
    from oandapyV20 import API
    from oandapyV20.endpoints.orders import OrderCreate
except ImportError:
    API = None
    OrderCreate = None

from shared.config import settings
from shared.logger import get_logger
from shared.models import (
    OrderRequest,
    OrderExecution,
    OrderStatus,
    OrderSide,
)

log = get_logger(__name__)


class OrderManager:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.postgres_dsn
        self._conn = None
        self._api = None

        if (
            API is not None
            and settings.oanda_api_key
            and settings.oanda_api_key != "tu_api_key_aqui"
            and not settings.dry_run
        ):
            try:
                self._api = API(
                    access_token=settings.oanda_api_key,
                    environment=settings.oanda_environment,
                )
                log.info(f"OANDA Execution Engine conectado en modo [{settings.oanda_environment.upper()}]")
            except Exception as e:
                log.error(f"Error conectando con OANDA API: {e}. Activando modo Simulación.")
                self._api = None
        else:
            log.info("Execution Engine operando en modo SIMULACIÓN / PAPER TRADING (sin riesgo real).")

    def _get_connection(self):
        if psycopg2 is None:
            return None
        if self._conn is None or self._conn.closed:
            try:
                self._conn = psycopg2.connect(self.dsn)
                self._conn.autocommit = True
            except Exception:
                self._conn = None
        return self._conn

    def execute_order(self, request: OrderRequest) -> OrderExecution:
        """
        Envía la orden a OANDA o la ejecuta en el simulador local y la persiste en TimescaleDB.
        """
        if self._api and not settings.dry_run:
            return self._execute_live_oanda(request)
        else:
            return self._execute_simulated(request)

    def _execute_live_oanda(self, req: OrderRequest) -> OrderExecution:
        units = req.units if req.direction == OrderSide.BUY else -req.units
        order_body = {
            "order": {
                "instrument": req.pair,
                "units": str(units),
                "type": "MARKET",
                "positionFill": "DEFAULT",
                "timeInForce": "FOK",
            }
        }

        if req.stop_loss:
            order_body["order"]["stopLossOnFill"] = {
                "price": f"{req.stop_loss:.5f}",
                "timeInForce": "GTC",
            }
        if req.take_profit:
            order_body["order"]["takeProfitOnFill"] = {
                "price": f"{req.take_profit:.5f}",
                "timeInForce": "GTC",
            }

        try:
            r = OrderCreate(accountID=settings.oanda_account_id, data=order_body)
            response = self._api.request(r)
            log.info(f"Respuesta OANDA OrderCreate: {response}")

            fill = response.get("orderFillTransaction", {})
            fill_price = float(fill.get("price", req.entry_price or 1.0))
            order_id = str(fill.get("id", response.get("orderCreateTransaction", {}).get("id", "LIVE_UNKNOWN")))

            execution = OrderExecution(
                pair=req.pair,
                direction=req.direction,
                units=req.units,
                entry_price=fill_price,
                stop_loss=req.stop_loss,
                take_profit=req.take_profit,
                status=OrderStatus.FILLED,
                oanda_order_id=order_id,
                opened_at=datetime.now(timezone.utc),
                strategy_name=req.strategy_name,
                metadata={"oanda_response": response, "request_metadata": req.metadata},
            )
            self._persist_order(execution)
            return execution

        except Exception as e:
            log.exception(f"Fallo en ejecución OANDA Live para {req.pair}: {e}")
            execution = OrderExecution(
                pair=req.pair,
                direction=req.direction,
                units=req.units,
                entry_price=req.entry_price or 0.0,
                status=OrderStatus.REJECTED,
                oanda_order_id=None,
                strategy_name=req.strategy_name,
                metadata={"error": str(e)},
            )
            return execution

    def _execute_simulated(self, req: OrderRequest) -> OrderExecution:
        """Simula la ejecución inmediata a precio de mercado."""
        fill_price = req.entry_price if req.entry_price else 1.0850
        slippage = 0.00002 if req.direction == OrderSide.BUY else -0.00002
        fill_price = round(fill_price + slippage, 5)

        sim_id = f"SIM_{uuid.uuid4().hex[:8].upper()}"

        execution = OrderExecution(
            pair=req.pair,
            direction=req.direction,
            units=req.units,
            entry_price=fill_price,
            stop_loss=req.stop_loss,
            take_profit=req.take_profit,
            status=OrderStatus.FILLED,
            oanda_order_id=sim_id,
            opened_at=datetime.now(timezone.utc),
            strategy_name=req.strategy_name,
            metadata={"simulated": True, "request_metadata": req.metadata},
        )
        self._persist_order(execution)
        log.info(
            f"⚡ [SIMULACIÓN] Orden ejecutada con éxito: {execution.pair} {execution.direction.value.upper()} | "
            f"Unidades={execution.units} | Entrada={execution.entry_price} | Ticket={sim_id}"
        )
        return execution

    def _persist_order(self, ex: OrderExecution):
        """Guarda la orden ejecutada en la tabla 'orders' de TimescaleDB si la BD está disponible."""
        conn = self._get_connection()
        if not conn:
            return
        try:
            query = """
                INSERT INTO orders (pair, direction, units, entry_price, stop_loss, take_profit, status, oanda_order_id, opened_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id;
            """
            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        ex.pair,
                        ex.direction.value,
                        ex.units,
                        ex.entry_price,
                        ex.stop_loss,
                        ex.take_profit,
                        ex.status.value,
                        ex.oanda_order_id,
                        ex.opened_at,
                    ),
                )
                db_id = cur.fetchone()[0]
                ex.order_id = db_id
                log.info(f"Orden registrada en BD con ID={db_id}")
        except Exception as e:
            log.warning(f"No se pudo registrar la orden en la BD: {e}")

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
