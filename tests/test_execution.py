"""
Pruebas del execution-engine: ejecución idempotente y reconciliación contra el broker.
Usan un broker y un repositorio en memoria (sin MT5, BD ni Redis).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import pytest

from brokers import Broker
from order_manager import OrderManager
from reconciliation import OrderReconciliation
from shared.models import OrderRequest, OrderSide, OrderStatus


class FakeBroker(Broker):
    mode = "mt5"

    def __init__(self):
        self.sent: List[OrderRequest] = []
        self.next_result: Dict[str, Any] = {"ok": True, "ticket": "1001", "price": 1.08505, "sl": 1.08305, "tp": 1.08905, "volume": 0.5}
        self.open: Set[str] = set()
        self.closed: Dict[str, Dict[str, Any]] = {}
        self.by_order: Dict[int, Dict[str, Any]] = {}

    def connect(self): return True
    def account_summary(self): return None
    def symbol_spec(self, pair): return None

    def market_order(self, req):
        self.sent.append(req)
        return self.next_result

    def open_tickets(self): return set(self.open)
    def closed_result(self, row): return self.closed.get(row["broker_ticket"])
    def find_position_by_order(self, order_id): return self.by_order.get(order_id)


class FakeRepo:
    def __init__(self):
        self.rows: Dict[int, Dict[str, Any]] = {}

    def add(self, **row):
        row.setdefault("execution_mode", None)
        row.setdefault("updated_at", datetime.now(timezone.utc))
        self.rows[row["id"]] = row

    def claim(self, order_id, mode):
        r = self.rows.get(order_id)
        if not r or r["status"] != "pending":
            return False
        r.update(status="submitting", execution_mode=mode)
        return True

    def mark_filled(self, order_id, ticket, price, sl, tp, lots, units):
        self.rows[order_id].update(status="filled", broker_ticket=ticket, entry_price=price, stop_loss=sl,
                                   take_profit=tp, volume_lots=lots, units=units,
                                   opened_at=datetime.now(timezone.utc))

    def mark_failed(self, order_id, status, error):
        self.rows[order_id].update(status=status.value, error=error)

    def mark_closed(self, order_id, exit_price, pnl, exit_reason, closed_at):
        self.rows[order_id].update(status="closed", exit_price=exit_price, pnl=pnl, exit_reason=exit_reason, closed_at=closed_at)

    def rows_with_status(self, status, older_than_seconds=0):
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
        return [dict(r) for r in self.rows.values() if r["status"] == status and r["updated_at"] <= cutoff]


class FakeBus:
    def __init__(self):
        self.published: List[tuple] = []

    def publish(self, channel, message):
        self.published.append((channel, message))


def make_request(order_id=1, **overrides) -> OrderRequest:
    data = dict(order_id=order_id, pair="EUR_USD", direction=OrderSide.BUY, units=50_000, volume_lots=0.5,
                entry_price=1.0850, stop_loss=1.0830, take_profit=1.0890, strategy_name="unit_test")
    data.update(overrides)
    return OrderRequest(**data)


@pytest.fixture
def setup():
    broker, repo = FakeBroker(), FakeRepo()
    repo.add(id=1, pair="EUR_USD", direction="buy", units=50_000, volume_lots=0.5, status="pending")
    return broker, repo, OrderManager(broker, repo)


def test_executes_and_marks_filled(setup):
    broker, repo, mgr = setup
    ex = mgr.execute_order(make_request())
    assert ex.status == OrderStatus.FILLED
    assert ex.broker_ticket == "1001" and ex.entry_price == 1.08505
    assert repo.rows[1]["status"] == "filled" and repo.rows[1]["execution_mode"] == "mt5"


def test_duplicate_message_is_not_sent_twice(setup):
    broker, repo, mgr = setup
    mgr.execute_order(make_request())
    assert mgr.execute_order(make_request()) is None
    assert len(broker.sent) == 1


def test_broker_rejection_is_recorded(setup):
    broker, repo, mgr = setup
    broker.next_result = {"ok": False, "error": "retcode=10019 No money"}
    ex = mgr.execute_order(make_request())
    assert ex.status == OrderStatus.REJECTED and "No money" in ex.error
    assert repo.rows[1]["status"] == "rejected"


def test_expired_order_is_cancelled_without_sending(setup):
    broker, repo, mgr = setup
    old = make_request(timestamp=datetime.now(timezone.utc) - timedelta(minutes=30))
    ex = mgr.execute_order(old)
    assert ex.status == OrderStatus.CANCELLED
    assert broker.sent == []


def test_reconciliation_closes_position_from_broker_history(setup):
    broker, repo, mgr = setup
    mgr.execute_order(make_request())
    repo.rows[1]["strategy_name"] = "EMA_Crossover"
    repo.rows[1]["risk_amount"] = 100.0
    broker.closed["1001"] = {"exit_price": 1.08905, "pnl": 196.5, "exit_reason": "tp",
                             "closed_at": datetime.now(timezone.utc)}
    bus = FakeBus()
    OrderReconciliation(broker, repo, bus=bus).reconcile_orders()

    assert repo.rows[1]["status"] == "closed" and repo.rows[1]["pnl"] == 196.5
    trade = bus.published[-1][1]
    assert trade.exit_reason == "tp" and trade.strategy_name == "EMA_Crossover"
    assert trade.pips == pytest.approx(40.0) and trade.roi_pct == pytest.approx(196.5)


def test_reconciliation_keeps_open_positions(setup):
    broker, repo, mgr = setup
    mgr.execute_order(make_request())
    broker.open = {"1001"}
    OrderReconciliation(broker, repo, bus=FakeBus()).reconcile_orders()
    assert repo.rows[1]["status"] == "filled"


def test_reconciliation_recovers_interrupted_submission(setup):
    broker, repo, mgr = setup
    repo.rows[1].update(status="submitting", execution_mode="mt5",
                        updated_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    broker.by_order[1] = {"ticket": "2002", "price": 1.0851, "sl": 1.0831, "tp": 1.0891, "volume": 0.5}
    broker.open = {"2002"}
    OrderReconciliation(broker, repo, bus=FakeBus()).reconcile_orders()
    assert repo.rows[1]["status"] == "filled" and repo.rows[1]["broker_ticket"] == "2002"


def test_reconciliation_alerts_orphan_positions(setup):
    broker, repo, _ = setup
    broker.open = {"9999"}
    bus = FakeBus()
    recon = OrderReconciliation(broker, repo, bus=bus)
    recon.reconcile_orders()
    recon.reconcile_orders()
    orphans = [m for _, m in bus.published if isinstance(m, dict) and m.get("type") == "orphan_position"]
    assert len(orphans) == 1  # se avisa una sola vez


def test_mode_change_cancels_positions_from_other_mode(setup):
    broker, repo, _ = setup
    repo.add(id=2, pair="GBP_USD", direction="sell", units=10_000, volume_lots=0.1, status="filled",
             execution_mode="sim", broker_ticket="SIM_ABC")
    OrderReconciliation(broker, repo, bus=FakeBus()).reconcile_orders()
    assert repo.rows[2]["status"] == "cancelled"
