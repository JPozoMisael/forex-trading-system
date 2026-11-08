"""
Seguimiento de tendencia pre-registrado (config.yaml, tendencia): ruptura de
55 velas, salida por el canal de 20 o por el stop de 2 ATR, costes con swap.
"""
import numpy as np
import pandas as pd
import pytest

from tendencia import Params, channels, simulate

P = Params(entrada_velas=55, salida_velas=20, stop_atr=2.0, atr_periodo=20, financiamiento_anual_pct=3.0)


def _days(closes, wick=0.002):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="D", tz="UTC")
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c},
                        index=idx)


def test_buys_the_breakout_and_exits_on_the_20_day_channel():
    flat = [1.10 + 0.001 * np.sin(i) for i in range(80)]
    trend = list(np.linspace(1.10, 1.30, 60))          # tendencia alcista
    fall = list(np.linspace(1.30, 1.20, 30))           # se da vuelta
    t = simulate(_days(flat + trend + fall), "EUR_USD", P)
    first = t.iloc[0]
    assert first["side"] == "buy" and first["exit_reason"] == "canal"
    assert first["entry_time"] > _days(flat).index[-1]  # entra con la ruptura, no antes
    assert first["r"] > 2                               # la tendencia paga varias veces el riesgo


def test_stop_when_the_breakout_fails():
    flat = [1.10 + 0.001 * np.sin(i) for i in range(80)]
    t = simulate(_days(flat + [1.105, 1.110, 1.050, 1.040, 1.040]), "EUR_USD", P)
    first = t.iloc[0]
    assert first["side"] == "buy" and first["exit_reason"] == "stop"
    assert -1.3 < first["r"] < -1.0  # pierde ~1 R más costes


def test_channels_do_not_include_the_current_candle():
    df = _days(list(np.linspace(1.0, 1.5, 100)))
    ch = channels(df, P)
    assert (ch["hi_in"].iloc[60:] < df["high"].iloc[60:]).all()  # el máximo de antes, no el de hoy


def test_swap_makes_long_trades_cost_more():
    flat = [1.10 + 0.001 * np.sin(i) for i in range(80)]
    df = _days(flat + list(np.linspace(1.10, 1.30, 60)) + list(np.linspace(1.30, 1.20, 30)))
    with_swap = simulate(df, "EUR_USD", P).iloc[0]["r"]
    no_swap = simulate(df, "EUR_USD", Params(financiamiento_anual_pct=0.0)).iloc[0]["r"]
    assert no_swap > with_swap


def test_opposite_trade_loses_in_a_clean_trend():
    flat = [1.10 + 0.001 * np.sin(i) for i in range(80)]
    df = _days(flat + list(np.linspace(1.10, 1.30, 60)) + list(np.linspace(1.30, 1.20, 30)))
    first = simulate(df, "EUR_USD", P).iloc[0]
    assert first["r_opposite"] == pytest.approx(-1, abs=0.3)  # vender en plena subida toca su stop
