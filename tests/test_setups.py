"""
Setups pre-registrados (config.yaml, sección setups): cada regla hace lo que
dice el pre-registro, las ventas son el espejo, nada mira el futuro, y la
salida por tiempo del motor funciona.
"""
import numpy as np
import pandas as pd
import pytest

import strategies.setups as setups
from data_loader import generate_synthetic_forex_data
from engine import BacktestEngine
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy
from strategies.setups import SetupStrategy, setup_signals


def _df(rows, start="2026-03-02 06:00"):
    idx = pd.date_range(start, periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


def _ctx(df, **cols):
    """Contexto neutro (nada dispara) con las columnas indicadas."""
    n = len(df)
    base = {"atr": 1.0, "ema50": 0.0, "tend_h1": 0.0, "tend_h4": 0.0, "tend_d": 0.0, "hi": np.nan, "lo": np.nan,
            "fib_up": 0.0, "vela_envolvente": 0.0, "vela_martillo": 0.0, "min5": np.nan, "max5": np.nan,
            "bb_up": 1e9, "bb_low": -1e9, "rsi": 50.0, "ayer_max": 1e9, "ayer_min": -1e9, "hora_londres": 3}
    base.update(cols)
    return pd.DataFrame({k: (v if np.ndim(v) else [v] * n) for k, v in base.items()}, index=df.index)


def test_fibonacci_buy_in_the_golden_zone(monkeypatch):
    # Movimiento de 100 a 110; cierre en 104.5 = retroceso del 55 %; martillo; diario y H4 alcistas
    df = _df([(104, 105, 103, 104.5)] * 2)
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, hi=110.0, lo=100.0, fib_up=1.0, tend_d=1.0, tend_h4=1.0,
                                                            vela_martillo=[0.0, 1.0]))
    s = setup_signals(df, "fibonacci").iloc[-1]
    assert s["dir"] == 1 and s["sl"] == pytest.approx(100 - 0.25) and s["tp"] == pytest.approx(110 + 2.72)
    assert setup_signals(df, "fibonacci").iloc[0]["dir"] == 0  # sin vela de giro no entra


def test_fibonacci_needs_both_trends_and_the_zone(monkeypatch):
    df = _df([(104, 105, 103, 104.5), (107, 108, 106, 107.0)])  # la 2.ª está en el 30 %: fuera de la zona
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, hi=110.0, lo=100.0, fib_up=1.0, tend_d=[1.0, 1.0],
                                                            tend_h4=[0.0, 1.0], vela_martillo=1.0))
    assert setup_signals(df, "fibonacci")["dir"].tolist() == [0, 0]


def test_fibonacci_sell_is_the_mirror(monkeypatch):
    df = _df([(105.5, 106.5, 105, 105.5)])  # bajada de 110 a 100, retroceso del 55 %
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, hi=110.0, lo=100.0, fib_up=-1.0, tend_d=-1.0,
                                                            tend_h4=-1.0, vela_envolvente=-1.0))
    s = setup_signals(df, "fibonacci").iloc[0]
    assert s["dir"] == -1 and s["sl"] == pytest.approx(110.25) and s["tp"] == pytest.approx(100 - 2.72)


def test_ema50_bounce(monkeypatch):
    df = _df([(100.2, 101, 99.8, 100.8)])  # toca la EMA 50 (100) y cierra alcista encima
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, ema50=100.0, tend_d=1.0, tend_h4=1.0, tend_h1=1.0,
                                                            min5=99.5))
    s = setup_signals(df, "ema50").iloc[0]
    risk = 100.8 - (99.5 - 0.25)
    assert s["dir"] == 1 and s["tp"] == pytest.approx(100.8 + 2 * risk)


def test_london_breakout_only_first_hours_and_on_the_cross(monkeypatch):
    df = _df([(99, 99.5, 98.8, 99.4), (99.4, 100.6, 99.3, 100.5), (100.5, 101, 100.4, 100.9)])
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, ayer_max=100.0, hora_londres=[8, 9, 10]))
    sig = setup_signals(df, "ruptura_londres")
    assert sig["dir"].tolist() == [0, 1, 0]  # solo la vela que cruza; la siguiente ya estaba encima
    assert sig["sl"].iloc[1] == pytest.approx(99.3 - 0.25) and sig["tp"].iloc[1] == pytest.approx(100.5 + 2 * 1.45)
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, ayer_max=100.0, hora_londres=[11, 12, 13]))
    assert setup_signals(df, "ruptura_londres")["dir"].tolist() == [0, 0, 0]  # fuera de las 3 primeras horas


def test_london_breakout_skips_wide_stops(monkeypatch):
    df = _df([(99, 99.5, 98.8, 99.4), (99.4, 100.6, 97.0, 100.5)])  # riesgo 3.75 > 2 ATR
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, ayer_max=100.0, hora_londres=8))
    assert setup_signals(df, "ruptura_londres")["dir"].tolist() == [0, 0]


def test_bollinger_rsi_only_in_sideways_market(monkeypatch):
    df = _df([(100, 100.2, 98.5, 98.8), (98.8, 99.8, 98.7, 99.6)])  # cierra fuera y vuelve adentro
    ctx = dict(bb_low=99.0, rsi=[25.0, 35.0], min5=98.5)
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, tend_d=0.0, **ctx))
    s = setup_signals(df, "bollinger_rsi").iloc[1]
    assert s["dir"] == 1 and s["tp"] == pytest.approx(99.6 + 1.5 * (99.6 - 98.25))
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, tend_d=1.0, **ctx))
    assert setup_signals(df, "bollinger_rsi")["dir"].tolist() == [0, 0]


@pytest.mark.parametrize("name", setups.SETUP_NAMES)
def test_signals_never_look_ahead(name):
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=7000, granularity_minutes=60, seed=8)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    cut = 6000
    altered = df.copy()
    altered.iloc[cut:, :4] *= 1.3
    pd.testing.assert_frame_equal(setup_signals(df, name).iloc[:cut], setup_signals(altered, name).iloc[:cut])


def test_strategy_uses_precomputed_signals(monkeypatch):
    df = _df([(100.2, 101, 99.8, 100.8)] * 3)
    monkeypatch.setattr(setups, "_context", lambda d: _ctx(d, ema50=100.0, tend_d=1.0, tend_h4=1.0, tend_h1=1.0,
                                                            min5=99.5))
    monkeypatch.setattr(SetupStrategy, "min_candles_required", property(lambda self: 1))
    s = SetupStrategy("ema50").precompute(df)
    sig = s.evaluate(df, "EUR_USD")
    assert sig.direction == SignalDirection.BUY and sig.metadata == {"setup": "ema50"}
    with pytest.raises(ValueError):
        SetupStrategy("inventado")


class _BuyOnce(BaseStrategy):
    def __init__(self):
        super().__init__("compra_una_vez")
        self.done = False

    @property
    def min_candles_required(self):
        return 2

    def evaluate(self, df, pair):
        if self.done:
            return None
        self.done = True
        c = float(df["close"].iloc[-1])
        return Signal(pair=pair, strategy_name=self.name, direction=SignalDirection.BUY, timestamp=df.index[-1],
                      entry_price=c, stop_loss=c - 0.05, take_profit=c + 0.05)


def test_engine_time_exit():
    df = _df([(1.1, 1.1005, 1.0995, 1.1)] * 10)
    res = BacktestEngine(max_hold_bars=3).run(_BuyOnce(), df, "EUR_USD")
    t = res.trades[0]
    # Señal con la vela 2 cerrada, entrada en la apertura de la 3, sale al cierre de la 5 (3 velas abierta)
    assert t["entry_time"] == df.index[3] and t["exit_reason"] == "tiempo" and t["exit_time"] == df.index[5]
    res = BacktestEngine().run(_BuyOnce(), df, "EUR_USD")  # sin límite: como antes, hasta el final de los datos
    assert res.trades[0]["exit_reason"] == "end_of_data"
