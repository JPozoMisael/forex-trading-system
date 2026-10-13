"""
Pruebas unitarias del gestor de riesgo: dimensionamiento y reglas (fail-closed).
"""
from datetime import datetime, timedelta, timezone

import pytest

from position_sizing import calculate_lots, calculate_position_size, get_pip_size
from rules import evaluate_signal
from shared.config import settings
from shared.fx import simulated_symbol_spec
from shared.models import AccountSummary, Signal, SignalDirection, SymbolSpec

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=timezone.utc)


def make_account(**overrides) -> AccountSummary:
    data = dict(balance=10_000.0, equity=10_000.0, day_start_equity=10_000.0, timestamp=NOW, source="mt5")
    data.update(overrides)
    return AccountSummary(**data)


def make_signal(**overrides) -> Signal:
    data = dict(
        pair="EUR_USD",
        strategy_name="test",
        direction=SignalDirection.BUY,
        entry_price=1.0850,
        stop_loss=1.0830,
        take_profit=1.0890,
        timestamp=NOW - timedelta(minutes=5),
    )
    data.update(overrides)
    return Signal(**data)


@pytest.fixture
def eurusd_spec() -> SymbolSpec:
    return simulated_symbol_spec("EUR_USD", 1.0850)


# ---------------------------------------------------------------- sizing
def test_pip_size_detection():
    assert get_pip_size("EUR_USD") == 0.0001
    assert get_pip_size("USD_JPY") == 0.01
    assert get_pip_size("EUR_JPY") == 0.01


def test_lots_eurusd(eurusd_spec):
    # $100 de riesgo con SL de 20 pips: 1 pip/lote = $10 -> 0.5 lotes
    lots = calculate_lots(10_000, 1.0, 1.0850, 1.0830, eurusd_spec)
    assert lots == pytest.approx(0.5)


def test_lots_cross_pair_uses_broker_tick_value():
    # EUR_GBP con tick_value que entrega MT5 (ya convertido a USD por el broker)
    spec = SymbolSpec(
        pair="EUR_GBP", symbol="EURGBP", digits=5, point=0.00001, tick_size=0.00001,
        tick_value=1.27, contract_size=100_000, volume_min=0.01, volume_max=50, volume_step=0.01,
    )
    lots = calculate_lots(10_000, 1.0, 0.8500, 0.8480, spec)  # 20 pips
    # riesgo por lote = 200 ticks * 1.27 = 254 -> 100/254 = 0.39 (redondeo hacia abajo)
    assert lots == pytest.approx(0.39)
    assert lots * 254 <= 100


def test_lots_never_exceed_risk_when_below_minimum(eurusd_spec):
    # $5 de riesgo con SL de 100 pips -> 0.0005 lotes < mínimo 0.01 -> no opera
    assert calculate_lots(500, 1.0, 1.0850, 1.0750, eurusd_spec) == 0.0


def test_lots_round_down_to_step(eurusd_spec):
    lots = calculate_lots(10_000, 1.0, 1.0850, 1.0827, eurusd_spec)  # 23 pips -> 0.4347
    assert lots == pytest.approx(0.43)


def test_units_usdjpy_converted_to_usd():
    # $100 de riesgo, 50 pips en USD_JPY a 155: valor pip/unidad = 0.01/155 USD
    units = calculate_position_size(10_000, 1.0, 155.00, 154.50, "USD_JPY")
    assert units == pytest.approx(100 / (0.5 / 155.0), rel=0.01)


def test_units_cross_without_rate_returns_zero():
    assert calculate_position_size(10_000, 1.0, 0.85, 0.848, "EUR_GBP") == 0


# ---------------------------------------------------------------- reglas
def test_approval(eurusd_spec):
    a = evaluate_signal(make_signal(), make_account(), eurusd_spec, [], now=NOW)
    assert a.approved, a.reason
    assert a.recommended_lots == pytest.approx(0.5)
    assert a.recommended_units == 50_000
    assert a.stop_loss == 1.0830 and a.take_profit == 1.0890
    assert a.risk_amount_usd == pytest.approx(100.0)


def test_rejects_without_account_snapshot(eurusd_spec):
    a = evaluate_signal(make_signal(), None, eurusd_spec, [], now=NOW)
    assert not a.approved and "snapshot" in a.reason.lower()


def test_rejects_stale_account_snapshot(eurusd_spec):
    old = make_account(timestamp=NOW - timedelta(seconds=settings.account_snapshot_max_age_seconds + 5))
    assert not evaluate_signal(make_signal(), old, eurusd_spec, [], now=NOW).approved


def test_rejects_without_symbol_spec():
    a = evaluate_signal(make_signal(), make_account(), None, [], now=NOW)
    assert not a.approved and "especificación" in a.reason.lower()


def test_rejects_low_balance(eurusd_spec):
    a = evaluate_signal(make_signal(), make_account(balance=50, equity=50, day_start_equity=50), eurusd_spec, [], now=NOW)
    assert not a.approved and "insuficiente" in a.reason.lower()


def test_circuit_breaker_uses_equity(eurusd_spec):
    # Sin pérdidas cerradas pero con -6% flotante: debe bloquear igual
    acc = make_account(balance=10_000, equity=9_400, day_start_equity=10_000)
    a = evaluate_signal(make_signal(), acc, eurusd_spec, [], now=NOW)
    assert not a.approved and "CIRCUIT BREAKER" in a.reason


def test_rejects_duplicate_pair_and_max_positions(eurusd_spec):
    assert not evaluate_signal(make_signal(), make_account(), eurusd_spec, ["EUR_USD"], now=NOW).approved
    full = ["GBP_USD", "AUD_USD", "USD_JPY"][: settings.max_open_positions]
    assert not evaluate_signal(make_signal(), make_account(), eurusd_spec, full, now=NOW).approved


def test_rejects_expired_signal(eurusd_spec):
    sig = make_signal(timestamp=NOW - timedelta(hours=2))
    a = evaluate_signal(sig, make_account(), eurusd_spec, [], now=NOW)
    assert not a.approved and "expirada" in a.reason.lower()


def test_rejects_wide_spread(eurusd_spec):
    wide = eurusd_spec.model_copy(update={"spread_points": int((settings.max_spread_pips + 1) * 10)})
    a = evaluate_signal(make_signal(), make_account(), wide, [], now=NOW)
    assert not a.approved and "spread" in a.reason.lower()


def test_rejects_sl_on_wrong_side(eurusd_spec):
    sig = make_signal(stop_loss=1.0870)  # SL por encima de la entrada en un BUY
    assert not evaluate_signal(sig, make_account(), eurusd_spec, [], now=NOW).approved


def test_rejects_poor_risk_reward(eurusd_spec):
    sig = make_signal(take_profit=1.0860)  # 10 pips de TP vs 20 de SL
    a = evaluate_signal(sig, make_account(), eurusd_spec, [], now=NOW)
    assert not a.approved and "ratio" in a.reason.lower()


def test_rejects_signal_without_entry(eurusd_spec):
    assert not evaluate_signal(make_signal(entry_price=None), make_account(), eurusd_spec, [], now=NOW).approved
