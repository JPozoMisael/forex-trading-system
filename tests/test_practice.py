"""
Operación de práctica de cada reporte: elige la de mayor probabilidad aunque no
llegue al umbral, la aprueba el risk-manager como una señal normal, sale en el
reporte y se cuenta aparte del modelo.
"""
from datetime import timedelta

import pandas as pd
import pytest

from daily_report import ReportData, build_report
from data_loader import generate_synthetic_forex_data
from practice import pick_practice, practice_signal
from rules import evaluate_signal
from shared.fx import simulated_symbol_spec
from shared.models import PRACTICE_STRATEGY, AccountSummary, SignalDirection
from trade_report import build_trade_email


def _data(pair: str, p_long: float, p_short: float, evaluated_ago=timedelta(minutes=5)) -> ReportData:
    candles = generate_synthetic_forex_data(pair="EUR_USD", n_candles=300, granularity_minutes=60,
                                            seed=3)[["open", "high", "low", "close"]]
    candles.index = candles.index.tz_localize("UTC") if candles.index.tz is None else candles.index
    now = (candles.index[-1] + pd.Timedelta(hours=1, minutes=5)).to_pydatetime()
    close = float(candles["close"].iloc[-1])
    state = {"vela": candles.index[-1].isoformat(), "evaluada": (now - evaluated_ago).isoformat(),
             "estrategias": {"ML_LightGBM": {"p_long": p_long, "p_short": p_short, "umbral": 0.5, "senal": None,
                                             "cierre": close, "atr": 0.001, "sl_atr": 2.0, "tp_atr": 3.0,
                                             "horizonte_velas": 48}}}
    health = {k: {"status": "healthy", "detail": "ok"} for k in ("postgres", "redis", "broker", "datos", "estrategia")}
    health["overall_status"] = "UP"
    return ReportData(pair=pair, granularity="H1", now=now, candles=candles, health=health, strategy_state=state,
                      account={"balance": 10000.0, "equity": 10000.0, "currency": "USD"}, initial_balance=10000)


def test_picks_highest_probability_even_below_threshold():
    datas = [_data("EUR_USD", 0.41, 0.33), _data("USD_JPY", 0.36, 0.44)]
    pr = pick_practice(datas, datas[0].now)
    assert pr is not None and pr.pair == "USD_JPY" and not pr.buy and pr.prob == pytest.approx(0.44)
    # Mismos SL/TP que una señal real: 2 y 3 ATR
    assert pr.stop_loss == pytest.approx(pr.entry + 0.002) and pr.take_profit == pytest.approx(pr.entry - 0.003)


def test_skips_pairs_with_open_position_or_stale_analysis():
    busy = _data("EUR_USD", 0.48, 0.30)
    busy.open_orders = pd.DataFrame([{"direction": "buy", "entry_price": 1.1}])
    stale = _data("USD_JPY", 0.47, 0.30, evaluated_ago=timedelta(hours=5))
    ok = _data("XAU_USD", 0.35, 0.39)
    pr = pick_practice([busy, stale, ok], ok.now)
    assert pr is not None and pr.pair == "XAU_USD"
    assert pick_practice([busy, stale], ok.now) is None


def test_practice_signal_passes_risk_rules():
    d = _data("EUR_USD", 0.41, 0.33)
    sig = practice_signal(pick_practice([d], d.now), d.now)
    assert sig.strategy_name == PRACTICE_STRATEGY and sig.direction == SignalDirection.BUY
    account = AccountSummary(balance=10_000.0, equity=10_000.0, day_start_equity=10_000.0, timestamp=d.now,
                             source="simulated")
    res = evaluate_signal(sig, account, simulated_symbol_spec("EUR_USD", sig.entry_price), active_pairs=[], now=d.now)
    assert res.approved, res.reason


def test_report_shows_practice_and_counts_it_apart():
    d = _data("EUR_USD", 0.41, 0.33)
    d.closed_orders = pd.DataFrame([{"direction": "sell", "entry_price": 1.1, "exit_price": 1.098, "exit_reason": "tp",
                                     "pnl": 150.0, "r_multiple": 1.5, "opened_at": d.candles.index[-30],
                                     "closed_at": d.candles.index[-20]}])
    d.practice_closed = pd.DataFrame([{"direction": "buy", "entry_price": 1.1, "exit_price": 1.098, "exit_reason": "sl",
                                       "pnl": -100.0, "r_multiple": -1.0, "opened_at": d.candles.index[-10],
                                       "closed_at": d.candles.index[-5]}])
    pr = pick_practice([d], d.now)
    subject, html, text = build_report([d], practice=pr, practice_on=True)
    assert "🎯 práctica: COMPRA EUR/USD 41 %" in subject
    assert "Se abre ahora una COMPRA de EUR/USD" in text and "Operación de práctica" in html
    assert "Práctica acumulada: 1 cerradas, 0 ganadas (0 %), resultado -$100.00" in text
    # El acumulado del modelo no incluye la práctica
    assert "1 (1 ganadas, 100 %)" in text

    _, _, text_none = build_report([d], practice=None, practice_on=True)
    assert "Esta vez no se abre operación de práctica" in text_none
    _, html_off, _ = build_report([d])
    assert "Operación de práctica" not in html_off


def test_closed_practice_email_says_it_was_practice():
    t = {"pair": "EUR_USD", "direction": "buy", "entry_price": 1.1, "exit_price": 1.098, "stop_loss": 1.098,
         "take_profit": 1.103, "opened_at": pd.Timestamp("2026-11-11 13:00", tz="UTC"),
         "closed_at": pd.Timestamp("2026-11-11 16:00", tz="UTC"), "duration_minutes": 180, "pnl": -100.0,
         "exit_reason": "sl", "r": -1.0, "lots": 0.5, "strategy": PRACTICE_STRATEGY}
    subject, _, text = build_trade_email(t, has_chart=False)
    assert subject.startswith("[Práctica · Operación cerrada con pérdida]")
    assert "Como práctica del reporte, el sistema abrió una COMPRA" in text
