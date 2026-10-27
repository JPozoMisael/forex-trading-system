"""
Avisos de fallo y reporte diario del monitoring: reglas de cuándo algo está
caído (respetando el cierre del mercado), cuándo se envía el reporte y que
el reporte y su gráfico se generan con y sin operaciones.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from daily_report import (ReportData, active_sessions, build_report, fibonacci_levels, plot_chart, should_send,
                          trade_stats)
from health_check import candle_status, failure_message, market_open, overall_status, strategy_status

UTC = timezone.utc
TUESDAY = datetime(2026, 10, 27, 15, 5, tzinfo=UTC)


def _load_monitoring_main():
    path = Path(__file__).resolve().parent.parent / "services" / "monitoring" / "src" / "main.py"
    spec = importlib.util.spec_from_file_location("monitoring_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------- salud

def test_market_hours():
    assert market_open(TUESDAY)
    assert not market_open(datetime(2026, 10, 31, 12, tzinfo=UTC))       # sábado
    assert not market_open(datetime(2026, 10, 30, 21, 30, tzinfo=UTC))   # viernes de noche
    assert not market_open(datetime(2026, 11, 1, 20, tzinfo=UTC))        # domingo antes de abrir
    assert market_open(datetime(2026, 11, 1, 22, 30, tzinfo=UTC))


def test_candles_ok_when_last_closed_bar_is_recent():
    last = datetime(2026, 10, 27, 14, tzinfo=UTC)  # cerró a las 15:00
    assert candle_status(last, TUESDAY, 60)["status"] == "healthy"


def test_candles_stale_is_a_failure():
    last = datetime(2026, 10, 27, 12, tzinfo=UTC)  # cerró a las 13:00: falta la de las 13 y la de las 14
    st = candle_status(last, TUESDAY, 60)
    assert st["status"] == "unhealthy" and "sin velas nuevas" in st["detail"]


def test_no_candle_alert_on_weekend_or_right_after_open():
    friday_close = datetime(2026, 10, 30, 20, tzinfo=UTC)
    assert candle_status(friday_close, datetime(2026, 10, 31, 15, tzinfo=UTC), 60)["status"] == "n/a"
    assert candle_status(friday_close, datetime(2026, 11, 1, 23, 30, tzinfo=UTC), 60)["status"] == "n/a"


def test_strategy_must_evaluate_last_candle():
    last = datetime(2026, 10, 27, 14, tzinfo=UTC)
    ok = {"vela": last.isoformat(), "estrategias": {"ML_LightGBM": {"p_long": 0.4}}}
    behind = {"vela": (last - timedelta(hours=1)).isoformat(), "estrategias": {}}
    assert strategy_status(ok, last, TUESDAY, 60)["status"] == "healthy"
    later = TUESDAY + timedelta(minutes=20)  # 25 min después de cerrar la vela: fuera de la gracia
    assert strategy_status(behind, last, later, 60)["status"] == "unhealthy"
    # Dentro de la gracia (reentreno del modelo tras un redeploy) no es fallo
    assert strategy_status(behind, last, last + timedelta(minutes=70), 60)["status"] == "healthy"
    assert strategy_status({"vela": last.isoformat(), "estrategias": {"ML_LightGBM": {"error": True}}},
                           last, later, 60)["status"] == "unhealthy"
    assert strategy_status(None, last, TUESDAY, 60)["status"] == "n/a"


def test_overall_status_and_message():
    report = {"postgres": {"status": "healthy"}, "redis": {"status": "healthy"}, "broker": {"status": "healthy"},
              "datos": {"status": "n/a"}, "estrategia": {"status": "unhealthy", "detail": "no evaluó la vela"}}
    assert overall_status(report) == "DEGRADED"
    assert "Strategy-engine" in failure_message(report)
    report["estrategia"] = {"status": "healthy"}
    assert overall_status(report) == "UP"


def test_alerts_only_after_three_failed_checks_and_once_on_recovery():
    alerts = _load_monitoring_main().AlertState(confirm_checks=3)
    assert [alerts.update(s) for s in ["DEGRADED", "DEGRADED", "UP"]] == [None, None, None]  # redeploy: sin aviso
    assert [alerts.update(s) for s in ["DEGRADED"] * 4] == [None, None, "fallo", None]
    assert [alerts.update(s) for s in ["UP", "UP"]] == ["recuperado", None]


# ------------------------------------------------------------- reporte

def test_report_schedule():
    assert should_send(TUESDAY, None, 12)
    assert not should_send(TUESDAY, "2026-10-27", 12)                       # ya enviado hoy
    assert not should_send(TUESDAY.replace(hour=11), None, 12)              # todavía no es la hora
    assert not should_send(datetime(2026, 10, 31, 13, tzinfo=UTC), None, 12)  # sábado
    assert should_send(TUESDAY, "2026-10-26", 12)                           # recupera si estuvo caído a esa hora


def _candles(n=600):
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=n, granularity_minutes=60, seed=11)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df[["open", "high", "low", "close"]]


def test_fibonacci_levels_go_from_latest_extreme():
    idx = pd.date_range("2026-10-01", periods=4, freq="h", tz="UTC")
    up = pd.DataFrame({"open": 1, "high": [1.10, 1.12, 1.15, 1.14], "low": [1.00, 1.05, 1.10, 1.12], "close": 1}, index=idx)
    fib = fibonacci_levels(up)
    assert fib["tendencia"] == "alcista"
    levels = dict(fib["niveles"])
    assert levels[0.0] == pytest.approx(1.15) and levels[1.0] == pytest.approx(1.00)
    assert levels[0.5] == pytest.approx(1.075)


def test_sessions():
    assert active_sessions(TUESDAY.replace(hour=8)) == ["Londres"]
    assert active_sessions(TUESDAY.replace(hour=13)) == ["Londres", "Nueva York"]
    assert active_sessions(TUESDAY.replace(hour=2)) == []


def test_trade_stats():
    closed = pd.DataFrame({"pnl": [150.0, -100.0, -100.0, 150.0], "r_multiple": [1.5, -1.0, -1.0, 1.5]})
    s = trade_stats(closed, 10000)
    assert s["operaciones"] == 4 and s["ganadas"] == 2 and s["win_rate"] == 50
    assert s["pnl"] == 100 and s["profit_factor"] == pytest.approx(1.5) and s["expectativa_r"] == pytest.approx(0.25)
    assert s["max_dd_pct"] == pytest.approx(200 / 10150 * 100)
    assert trade_stats(pd.DataFrame(), 10000) == {"operaciones": 0}


def _report_data(with_trades: bool) -> ReportData:
    candles = _candles()
    now = candles.index[-1] + pd.Timedelta(hours=1, minutes=5)
    health = {k: {"status": "healthy", "detail": "ok"} for k in ("postgres", "redis", "broker", "datos", "estrategia")}
    health["overall_status"] = "UP"
    state = {"vela": candles.index[-1].isoformat(),
             "estrategias": {"ML_LightGBM": {"p_long": 0.41, "p_short": 0.38, "umbral": 0.5, "senal": None}}}
    open_orders = closed = pd.DataFrame()
    if with_trades:
        c = float(candles["close"].iloc[-1])
        open_orders = pd.DataFrame([{"direction": "buy", "entry_price": c, "stop_loss": c - 0.004,
                                     "take_profit": c + 0.006, "opened_at": candles.index[-5]}])
        closed = pd.DataFrame([{"direction": "sell", "entry_price": c + 0.002, "exit_price": c - 0.001,
                                "exit_reason": "tp", "pnl": 120.0, "r_multiple": 1.2,
                                "opened_at": candles.index[-30], "closed_at": candles.index[-3]}])
    return ReportData(pair="EUR_USD", granularity="H1", now=now.to_pydatetime(), candles=candles, health=health,
                      strategy_state=state, account={"balance": 10120.0, "equity": 10150.0, "currency": "USD"},
                      open_orders=open_orders, closed_orders=closed, initial_balance=10000)


@pytest.mark.parametrize("with_trades", [False, True])
def test_report_and_chart_are_built(with_trades):
    data = _report_data(with_trades)
    subject, html, text = build_report(data)
    assert "sistema OK" in subject and "cid:grafico" in html
    assert "P(compra gana) 41.0 %" in text
    if with_trades:
        assert "1 posición(es) abierta(s)" in subject and "+$120.00" in text
    else:
        assert "ninguna todavía" in text
    png = plot_chart(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000
