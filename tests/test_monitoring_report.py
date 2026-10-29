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
from daily_report import (ReportData, active_sessions, build_report, due_slot, fibonacci_levels, plot_chart,
                          trade_stats, upcoming_sessions)
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

TIMES = ["01:30", "08:00"]  # hora de Ecuador (UTC-5) = 06:30 y 13:00 UTC


def test_report_slots_before_london_and_new_york():
    at = lambda h, m: datetime(2026, 10, 27, h, m, tzinfo=UTC)
    assert due_slot(at(6, 29), None, TIMES) is None
    assert due_slot(at(6, 30), None, TIMES) == "2026-10-27 01:30"
    assert due_slot(at(6, 31), "2026-10-27 01:30", TIMES) is None          # ya enviado
    assert due_slot(at(8, 0), None, TIMES) == "2026-10-27 01:30"           # recupera dentro de 2 h
    assert due_slot(at(9, 0), None, TIMES) is None                         # demasiado tarde para Londres
    assert due_slot(at(13, 5), "2026-10-27 01:30", TIMES) == "2026-10-27 08:00"
    assert due_slot(datetime(2026, 10, 31, 6, 30, tzinfo=UTC), None, TIMES) is None  # sábado
    assert upcoming_sessions(at(6, 30)) == ["Londres"]
    assert upcoming_sessions(at(11, 0)) == ["Nueva York"]
    assert upcoming_sessions(at(13, 0)) == ["Nueva York"]  # el reporte de las 8:00 de Ecuador


def test_one_email_per_slot_through_real_redis_round_trip():
    """El fallo que mandaba un correo cada minuto: el turno enviado se guardaba y no se podía leer."""
    fakeredis = pytest.importorskip("fakeredis")
    from shared.bus import EventBus

    bus = EventBus()
    bus._client = fakeredis.FakeRedis(decode_responses=True)
    monitoring = _load_monitoring_main()
    sent = []
    start = datetime(2026, 10, 27, 6, 0, tzinfo=UTC)
    for minute in range(0, 8 * 60):  # de 06:00 a 14:00, un chequeo por minuto
        now = start + timedelta(minutes=minute)
        monitoring._maybe_send_report(bus, None, None, None, now=now,
                                      send=lambda *args: sent.append(args[-1]))
    assert [t.strftime("%H:%M") for t in sent] == ["06:30", "13:00"]


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
    assert "sistema OK" in subject and "cid:grafico_EUR_USD" in html
    assert "Probabilidad de que una COMPRA llegue a su objetivo: 41 %" in text and "Sin señal" in text
    assert "UTC" not in text  # todo en hora local
    if with_trades:
        assert "1 posición(es) abierta(s)" in subject and "+$120.00" in text
    else:
        assert "ninguna todavía" in text
    png = plot_chart(data)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 20_000


def test_combined_status_names_the_failing_pair():
    from health_check import combine_status

    ok = {"status": "healthy", "detail": "última vela 14:00"}
    st = combine_status({"EUR_USD": ok, "XAU_USD": {"status": "unhealthy", "detail": "sin velas nuevas"}})
    assert st["status"] == "unhealthy" and "XAU_USD" in st["detail"] and "EUR_USD" not in st["detail"]
    assert combine_status({"EUR_USD": ok, "GBP_USD": ok})["status"] == "healthy"
    assert combine_status({"EUR_USD": {"status": "n/a", "detail": "mercado cerrado"}})["status"] == "n/a"


def test_report_with_several_instruments():
    eur = _report_data(with_trades=True)
    gold_candles = eur.candles * 3600  # precios del orden del oro
    gold = ReportData(pair="XAU_USD", granularity="H1", now=eur.now, candles=gold_candles, health=eur.health,
                      strategy_state={"vela": gold_candles.index[-1].isoformat(),
                                      "estrategias": {"ML_LightGBM": {"p_long": 0.55, "p_short": 0.2,
                                                                      "umbral": 0.5, "senal": "buy"}}},
                      account=eur.account, initial_balance=10000)
    subject, html, text = build_report([eur, gold])
    assert "Oro (XAU/USD) SEÑAL COMPRA" in subject and "1 posición(es)" in subject
    assert "cid:grafico_EUR_USD" in html and "cid:grafico_XAU_USD" in html
    assert f"precio {gold_candles['close'].iloc[-1]:.2f}" in text  # oro con 2 decimales
    assert plot_chart(gold)[:4] == b"\x89PNG"


def test_local_times_for_ecuador():
    from shared.timefmt import fmt_hora, fmt_local

    assert fmt_hora(datetime(2026, 10, 28, 10, 0, tzinfo=UTC)) == "5:00 a. m."   # la confusión de "10:00"
    assert fmt_hora(datetime(2026, 10, 28, 13, 0, tzinfo=UTC)) == "8:00 a. m."
    assert fmt_hora(datetime(2026, 10, 28, 21, 30, tzinfo=UTC)) == "4:30 p. m."
    assert fmt_local("2026-10-28T03:00:00+00:00") == "mar 27/10 10:00 p. m."     # cambia el día


def test_open_position_explained_in_words():
    data = _report_data(with_trades=True)
    c = float(data.candles["close"].iloc[-1])
    data.open_orders["units"] = 25_000  # SL a 40 pips -> ~$100; TP a 60 pips -> ~$150
    _, html, text = build_report(data)
    assert "COMPRA abierta el" in text and "perdiendo ~$100" in text and "ganando ~$150" in text
    assert "No tienes que hacer nada" in text and "Ahora va +$0.00" in text
    assert "hora de Ecuador" in html


def test_repeated_signal_with_open_position_is_not_emailed():
    m = _load_monitoring_main()
    s = m.settings
    open_pos = lambda pair, when: pair == "EUR_USD"
    assert m.is_redundant(s.channel_risk_alerts, {"reason": "Ya existe una posición activa para el par EUR_USD"}, open_pos)
    assert not m.is_redundant(s.channel_risk_alerts, {"reason": "Spread demasiado alto"}, open_pos)
    assert m.is_redundant(s.channel_signals_new, {"pair": "EUR_USD", "timestamp": "2026-10-28T14:00:00+00:00"}, open_pos)
    assert not m.is_redundant(s.channel_signals_new, {"pair": "XAU_USD"}, open_pos)   # señal nueva: se avisa
    assert not m.is_redundant(s.channel_orders_closed, {"pair": "EUR_USD"}, open_pos)  # cierres siempre


def test_instrument_without_model_says_it_does_not_trade():
    data = _report_data(with_trades=False)
    data.pair = "GBP_USD"
    data.strategy_state = {"vela": data.candles.index[-1].isoformat(), "estrategias": {}}
    subject, _, text = build_report(data)
    assert "NO pasó la prueba del modelo" in text and "no opera aquí" in text
    assert "sin señales" in subject


def test_reference_of_each_instrument_comes_from_config():
    from daily_report import _ref_text, backtest_refs

    refs = backtest_refs()
    assert {"EUR_USD", "USD_JPY", "XAU_USD"} <= set(refs)
    assert "comprar y mantener: +103.4 %" in _ref_text("XAU_USD", refs)
    assert _ref_text("GBP_USD", refs) == ""


# ------------------------------------------------------------- gráficos de temporalidades y de operación

def _fake_fetch(candles):
    def fetch(pair, gran, start, end):
        return candles[(candles.index >= start) & (candles.index < end)]
    return fetch


def test_report_includes_every_timeframe():
    from charts import plot_timeframes
    from daily_report import collect_timeframes, report_images

    data = _report_data(with_trades=True)
    fetch = _fake_fetch(data.candles)  # las mismas velas sirven para probar todas las temporalidades
    data.timeframes = collect_timeframes("EUR_USD", data.now, skip="H1", fetch=fetch)
    assert set(data.timeframes) == {"D", "H4", "M30", "M15"}
    subject, html, text = build_report(data)
    assert "cid:tf_EUR_USD" in html and "Todas las temporalidades" in html
    assert "1 hora (la del modelo): tendencia" in text and "pips" in text
    images = report_images([data])
    assert set(images) == {"grafico_EUR_USD", "tf_EUR_USD"}
    assert plot_timeframes("EUR_USD", data.timeframes)[:4] == b"\x89PNG"


def test_report_still_sent_when_timeframes_fail():
    from daily_report import collect_timeframes

    def broken(*a, **k):
        raise ConnectionError("sin internet")

    data = _report_data(with_trades=False)
    data.timeframes = collect_timeframes("EUR_USD", data.now, skip="H1", fetch=broken)
    assert data.timeframes == {}
    _, html, _ = build_report(data)
    assert "cid:tf_EUR_USD" not in html


def test_gold_moves_are_in_dollars_and_forex_in_pips():
    from charts import duration_text, move_text

    assert move_text("EUR_USD", 0.00153) == "15.3 pips"
    assert move_text("USD_JPY", -0.25, signed=True) == "−25.0 pips"
    assert move_text("XAU_USD", 12.5) == "12.50 USD/oz"
    assert duration_text(359.3) == "5 h 59 min" and duration_text(45) == "45 min" and duration_text(3000) == "2 d 2 h"


def _closed_trade(candles):
    entry_t, exit_t = candles.index[-20], candles.index[-8]
    entry = float(candles["close"].loc[entry_t])
    return {"order_id": 7, "pair": "EUR_USD", "direction": "sell", "units": 65000, "entry_price": entry,
            "exit_price": entry + 0.00153, "pnl": -99.45, "pips": -15.3, "roi_pct": -100.0,
            "opened_at": entry_t.isoformat(), "closed_at": exit_t.isoformat(), "duration_minutes": 720.0,
            "strategy_name": "ML_LightGBM", "exit_reason": "sl"}, \
        {"stop_loss": entry + 0.00153, "take_profit": entry - 0.0023, "risk_amount": 99.45, "volume_lots": 0.65}


class _FakeEmail:
    def __init__(self):
        self.html, self.text_alerts = [], []

    def send_html(self, subject, html, text, images=None):
        self.html.append((subject, html, text, images or {}))
        return True

    def send_trade_closed_alert(self, trade):
        self.text_alerts.append(trade)


def test_closed_trade_email_has_chart_and_explains_the_result():
    from trade_report import send_trade_report

    candles = _report_data(False).candles
    trade, order = _closed_trade(candles)
    email = _FakeEmail()
    now = candles.index[-1].to_pydatetime() + timedelta(hours=1)
    assert send_trade_report(email, trade, now=now, load=lambda _id: order, fetch=_fake_fetch(candles))
    subject, html, text, images = email.html[0]
    assert "EUR/USD VENTA" in subject and "Stop Loss" in subject and "−$99.45" in subject
    assert "esperando que el precio bajara" in text and "15.3 pips" in text and "1 : 1.50" in text
    assert "-1.00 R" in text and "UTC" not in text
    assert images["operacion"][:4] == b"\x89PNG" and "cid:operacion" in html


def test_closed_trade_email_without_chart_or_db_still_goes_out():
    from trade_report import send_trade_report

    candles = _report_data(False).candles
    trade, _ = _closed_trade(candles)
    email = _FakeEmail()

    def broken(*a, **k):
        raise ConnectionError("sin internet")

    assert send_trade_report(email, trade, load=lambda _id: None, fetch=broken)
    subject, html, text, images = email.html[0]
    assert images == {} and "cid:operacion" not in html and "−$99.45" in subject


# ------------------------------------------------------------- calendario económico

CAL = [
    {"title": "Federal Funds Rate", "country": "USD", "date": "2026-10-28T14:00:00-04:00", "impact": "High",
     "forecast": "4.00%", "previous": "4.00%"},
    {"title": "Advance GDP q/q", "country": "USD", "date": "2026-10-29T08:30:00-04:00", "impact": "High",
     "forecast": "2.2%", "previous": "3.2%"},
    {"title": "Main Refinancing Rate", "country": "EUR", "date": "2026-10-29T08:15:00-04:00", "impact": "High",
     "forecast": "2.15%", "previous": "2.15%"},
    {"title": "CPI y/y", "country": "AUD", "date": "2026-10-28T20:30:00-04:00", "impact": "High",
     "forecast": "4.2%", "previous": "4.0%"},
    {"title": "Retail Sales m/m", "country": "USD", "date": "2026-10-29T08:30:00-04:00", "impact": "Medium",
     "forecast": "0.3%", "previous": "0.1%"},
]


def test_calendar_keeps_high_impact_news_of_traded_currencies():
    from econ_calendar import upcoming

    now = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)  # jue 1:30 a. m. en Ecuador
    events = upcoming(CAL, ["EUR_USD", "XAU_USD"], now)
    # Fed ya pasó (ayer), AUD no se sigue, ventas minoristas es impacto medio
    assert [e["titulo"] for e in events] == ["Decisión de tasas del BCE", "PIB adelantado (trimestral)"]
    assert events[1]["afecta"] == ["EUR_USD", "XAU_USD"]  # el oro se mueve con el dólar
    assert events[0]["afecta"] == ["EUR_USD"]


def test_report_shows_news_in_local_time():
    from econ_calendar import upcoming

    data = _report_data(with_trades=False)
    now = datetime(2026, 10, 29, 6, 30, tzinfo=UTC)
    data.now = now
    subject, html, text = build_report(data, upcoming(CAL, ["EUR_USD"], now))
    assert "📰 Decisión de tasas del BCE 7:15 a. m." in subject
    assert "jue 29/10 7:30 a. m. · USD · PIB adelantado (trimestral) (previsión 2.2%, anterior 3.2%) → EUR/USD" in text
    assert "Noticias de impacto alto" in html


def test_report_still_goes_out_when_calendar_fails():
    from econ_calendar import calendar_for_report

    def broken():
        raise ConnectionError("sin internet")

    data = _report_data(with_trades=False)
    events = calendar_for_report(["EUR_USD"], data.now, fetch=broken)
    assert events is None
    _, _, text = build_report(data, events)
    assert "No se pudo leer el calendario" in text
    assert "Sin noticias de impacto alto" in build_report(data)[2]


def test_sessions_follow_daylight_saving_of_each_city():
    from daily_report import sessions_local_text

    # Julio: Londres y Nueva York en horario de verano
    assert sessions_local_text(datetime(2026, 7, 7, 12, tzinfo=UTC)).startswith(
        "Londres abre 2:00 a. m. y cierra 11:00 a. m.; Nueva York abre 7:00 a. m.")
    # 29 oct: Londres ya cambió (25 oct), Nueva York todavía no (1 nov)
    assert sessions_local_text(datetime(2026, 10, 29, 12, tzinfo=UTC)).startswith(
        "Londres abre 3:00 a. m. y cierra 12:00 p. m.; Nueva York abre 7:00 a. m.")
    # Noviembre: los dos en horario de invierno
    nov = datetime(2026, 11, 3, 13, 5, tzinfo=UTC)  # mar 8:05 a. m. en Ecuador
    assert "Nueva York abre 8:00 a. m. y cierra 5:00 p. m." in sessions_local_text(nov)
    assert active_sessions(nov) == ["Londres", "Nueva York"] and upcoming_sessions(nov) == ["Nueva York"]
    assert active_sessions(datetime(2026, 11, 3, 12, 30, tzinfo=UTC)) == ["Londres"]  # NY aún cerrada
