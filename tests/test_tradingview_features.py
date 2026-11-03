"""
Herramientas de TradingView como datos: cada una usa solo velas cerradas y
lee el gráfico como lo haría un trader.
"""
import numpy as np
import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from indicators import calculate_atr
from ml.tradingview import (TV_COLUMNS, candle_patterns, fibonacci, higher_tf_emas, sessions, trend,
                            tradingview_features)


@pytest.fixture(scope="module")
def candles():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=6000, granularity_minutes=60, seed=4)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df


def test_columns_and_no_future(candles):
    full = tradingview_features(candles)
    assert list(full.columns) == list(TV_COLUMNS)
    cut = 4000
    altered = candles.copy()
    altered.iloc[cut:, :4] *= 1.4
    pd.testing.assert_frame_equal(full.iloc[:cut], tradingview_features(altered).iloc[:cut])
    pd.testing.assert_frame_equal(full.iloc[:cut], tradingview_features(candles.iloc[:cut]))


def test_h4_uses_only_closed_h4_candles(candles):
    """La vela H4 de 8:00-12:00 se usa desde la vela H1 de 11:00 (que cierra a las 12:00), no antes."""
    e = higher_tf_emas(candles, "4h")
    t = candles.index[(candles.index.hour == 11)][100]
    bin_start = t.floor("4h")  # 8:00
    altered = candles.copy()
    altered.loc[altered.index >= bin_start + pd.Timedelta(hours=3), "close"] *= 1.5  # cambia el cierre del bin
    e2 = higher_tf_emas(altered, "4h")
    before = t - pd.Timedelta(hours=1)  # vela de 10:00: aún no conoce el bin 8-12
    assert e.loc[before, "ema50"] == e2.loc[before, "ema50"]
    assert e.loc[t, "ema50"] != e2.loc[t, "ema50"]


def test_trend_rule_is_the_same_as_the_report():
    c, e50, e200 = (pd.Series(v) for v in ([3.0, 1.0, 2.0], [2.0, 2.0, 1.0], [1.0, 3.0, 3.0]))
    assert trend(c, e50, e200).tolist() == [1.0, -1.0, 0.0]


def test_fibonacci_retracement():
    # Sube de 1.0 a 2.0 y retrocede a 1.5: movimiento de subida, retroceso del 50 % (justo en un nivel)
    highs = np.r_[np.linspace(1.0, 2.0, 10), [1.6, 1.5]]
    idx = pd.date_range("2024-01-01", periods=len(highs), freq="h", tz="UTC")
    df = pd.DataFrame({"open": highs, "high": highs, "low": highs, "close": highs}, index=idx)
    df.iloc[0, df.columns.get_loc("low")] = 1.0
    fib = fibonacci(df, pd.Series(0.1, index=idx), bars=12).iloc[-1]
    assert fib["fib_mov"] == 1.0 and fib["fib_retroceso"] == pytest.approx(0.5) and fib["fib_dist_nivel"] == pytest.approx(0)


def test_sessions_follow_daylight_saving():
    # 7:30 UTC: en verano ya es Londres (8:30 BST); en invierno todavía Asia (7:30 GMT)
    idx = pd.DatetimeIndex(["2026-07-01 07:00", "2026-01-14 07:00", "2026-07-01 13:00", "2026-07-01 18:00"], tz="UTC")
    assert sessions(idx).tolist() == [1.0, 0.0, 2.0, 3.0]


def test_candle_patterns():
    idx = pd.date_range("2024-01-01", periods=3, freq="h", tz="UTC")
    df = pd.DataFrame({"open": [1.10, 1.09, 1.20], "high": [1.11, 1.12, 1.21], "low": [1.08, 1.085, 1.10],
                       "close": [1.09, 1.115, 1.205]}, index=idx)
    p = candle_patterns(df)
    assert p["vela_envolvente"].tolist() == [0.0, 1.0, 0.0]   # la 2.ª envuelve a la 1.ª bajista
    assert p["vela_martillo"].iloc[2] == 1.0                    # mecha inferior larga, cuerpo arriba
