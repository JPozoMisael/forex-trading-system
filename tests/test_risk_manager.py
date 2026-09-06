"""
Pruebas unitarias para el gestor y dimensionamiento de riesgo institucional.
"""
from datetime import datetime, timezone
import pytest

from position_sizing import calculate_position_size, get_pip_size
from rules import RiskRulesEngine
from shared.models import Signal, SignalDirection


def test_pip_size_detection():
    assert get_pip_size("EUR_USD") == 0.0001
    assert get_pip_size("GBP_USD") == 0.0001
    assert get_pip_size("USD_JPY") == 0.01
    assert get_pip_size("EUR_JPY") == 0.01


def test_position_sizing_calculation():
    # Balance: $10,000, Riesgo: 1% ($100), Entrada: 1.0850, SL: 1.0830 (20 pips)
    units = calculate_position_size(
        account_balance=10000.0,
        risk_pct=1.0,
        entry_price=1.0850,
        stop_loss_price=1.0830,
        pair="EUR_USD",
    )
    # $100 / (20 pips * $0.0001) = 50,000 unidades
    assert units == pytest.approx(50000, rel=0.05)


def test_position_sizing_jpy_pair():
    # Balance: $10,000, Riesgo: 1% ($100), Entrada: 155.00, SL: 154.50 (50 pips)
    units = calculate_position_size(
        account_balance=10000.0,
        risk_pct=1.0,
        entry_price=155.00,
        stop_loss_price=154.50,
        pair="USD_JPY",
    )
    assert units > 0


def test_risk_rules_insufficient_balance():
    engine = RiskRulesEngine(dsn="postgresql://mock:mock@localhost:5432/mock")
    sig = Signal(
        pair="EUR_USD",
        strategy_name="test",
        direction=SignalDirection.BUY,
        entry_price=1.0850,
        stop_loss=1.0830,
        take_profit=1.0890,
    )
    # Forzar evaluación con balance muy bajo
    assessment = engine.evaluate_signal(sig, account_balance=50.0)
    assert not assessment.approved
    assert "insuficiente" in assessment.reason.lower()


def test_risk_rules_approval():
    engine = RiskRulesEngine(dsn="postgresql://mock:mock@localhost:5432/mock")
    sig = Signal(
        pair="EUR_USD",
        strategy_name="test",
        direction=SignalDirection.BUY,
        entry_price=1.0850,
        stop_loss=1.0830,
        take_profit=1.0890,
    )
    assessment = engine.evaluate_signal(sig, account_balance=10000.0)
    assert assessment.approved
    assert assessment.recommended_units > 0
    assert assessment.stop_loss == 1.0830
    assert assessment.take_profit == 1.0890
