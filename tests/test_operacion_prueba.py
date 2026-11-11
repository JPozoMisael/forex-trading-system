"""
La operación de prueba la aprueba el risk-manager como una señal normal, y el
script se niega a correr fuera de simulación.
"""
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from rules import evaluate_signal
from shared.fx import simulated_symbol_spec
from shared.models import TEST_STRATEGY, AccountSummary, SignalDirection

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)


def _script():
    spec = importlib.util.spec_from_file_location("operacion_prueba", ROOT / "scripts" / "operacion_prueba.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("buy", [True, False])
def test_test_signal_is_approved_by_risk_rules(buy):
    sig = _script().build_test_signal("GBP_USD", buy, 1.33100, 0.0011, now=NOW)
    assert sig.strategy_name == TEST_STRATEGY
    assert sig.direction == (SignalDirection.BUY if buy else SignalDirection.SELL)
    # stop a 0.5 ATR del lado correcto
    assert sig.stop_loss == pytest.approx(1.33100 - (1 if buy else -1) * 0.00055)

    account = AccountSummary(balance=10_000.0, equity=10_000.0, day_start_equity=10_000.0, timestamp=NOW, source="simulated")
    res = evaluate_signal(sig, account, simulated_symbol_spec("GBP_USD", 1.331), active_pairs=[], now=NOW)
    assert res.approved, res.reason
    assert res.risk_percent <= 1.0


def test_test_signal_rejects_bad_atr():
    with pytest.raises(ValueError):
        _script().build_test_signal("GBP_USD", True, 1.331, 0.0, now=NOW)


def test_refuses_without_dry_run(monkeypatch):
    script = _script()
    monkeypatch.setattr(script, "settings", SimpleNamespace(dry_run=False, forex_pairs=["GBP_USD"]))
    monkeypatch.setattr("sys.argv", ["operacion_prueba.py"])
    with pytest.raises(SystemExit, match="DRY_RUN"):
        script.main()
