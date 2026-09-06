"""
Pruebas unitarias para el motor de ejecución de órdenes y reconciliación.
"""
from datetime import datetime, timezone
import pytest

from order_manager import OrderManager
from shared.models import OrderRequest, OrderSide, OrderStatus


def test_simulated_order_execution():
    manager = OrderManager(dsn="postgresql://mock:mock@localhost:5432/mock")
    req = OrderRequest(
        pair="EUR_USD",
        direction=OrderSide.BUY,
        units=10000,
        entry_price=1.0850,
        stop_loss=1.0830,
        take_profit=1.0890,
        strategy_name="unit_test",
    )
    execution = manager.execute_order(req)
    assert execution.pair == "EUR_USD"
    assert execution.direction == OrderSide.BUY
    assert execution.units == 10000
    assert execution.status == OrderStatus.FILLED
    assert execution.oanda_order_id.startswith("SIM_")
    assert execution.entry_price > 0
