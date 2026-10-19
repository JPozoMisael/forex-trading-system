"""
Pruebas de las reglas de ejecución del backtester: spread completo, slippage en
todas las salidas, comisión, entrada a la apertura siguiente, prioridad del SL
y límite de pérdida diaria.
"""
from typing import Dict, Optional

import pandas as pd
import pytest

from engine import BacktestEngine
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy

PIP = 0.0001


class ScriptedStrategy(BaseStrategy):
    """Emite la señal indicada cuando la última vela cerrada es la del índice dado."""

    def __init__(self, signals: Dict[int, SignalDirection], sl_pips: float = 10.0, tp_pips: float = 10.0, every_bar=None):
        super().__init__(name="Scripted")
        self.signals = signals
        self.sl_pips = sl_pips
        self.tp_pips = tp_pips
        self.every_bar = every_bar
        self.df_index = None

    @property
    def min_candles_required(self) -> int:
        return 2

    @property
    def lookback_window(self) -> int:
        return 2

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        pos = self.df_index.get_loc(df.index[-1])
        direction = self.every_bar or self.signals.get(pos)
        if direction is None:
            return None
        close = float(df["close"].iloc[-1])
        sign = 1 if direction == SignalDirection.BUY else -1
        return Signal(
            pair=pair,
            strategy_name=self.name,
            direction=direction,
            timestamp=df.index[-1],
            entry_price=close,
            stop_loss=close - sign * self.sl_pips * PIP,
            take_profit=close + sign * self.tp_pips * PIP,
        )


def make_df(bars, start="2024-01-02 00:00"):
    """bars: lista de (open, high, low, close) en velas H1."""
    idx = pd.date_range(start, periods=len(bars), freq="h", tz="UTC")
    df = pd.DataFrame(bars, columns=["open", "high", "low", "close"], index=idx)
    df["volume"] = 100.0
    return df


def flat(price=1.1000, n=1):
    return [(price, price, price, price)] * n


def run(df, strategy, **engine_kwargs):
    strategy.df_index = df.index
    params = dict(initial_balance=10_000.0, risk_per_trade_pct=1.0, spread_pips=0.0, slippage_pips=0.0)
    params.update(engine_kwargs)
    return BacktestEngine(**params).run(strategy, df, pair="EUR_USD")


def test_full_spread_is_paid_on_entry_and_exit():
    # Precio plano: el único costo de ida y vuelta es el spread completo (2 pips).
    df = make_df(flat(n=6))
    for direction in (SignalDirection.BUY, SignalDirection.SELL):
        res = run(df, ScriptedStrategy({2: direction}), spread_pips=2.0)
        trade = res.trades[0]
        assert trade["exit_reason"] == "end_of_data"
        assert trade["pips"] == pytest.approx(-2.0)


def test_slippage_is_paid_on_take_profit():
    # BUY a 1.1000 sin spread; la vela siguiente alcanza el TP (+10 pips).
    bars = flat(n=3) + [(1.1000, 1.1020, 1.1000, 1.1015)] + flat(1.1015, 2)
    res = run(make_df(bars), ScriptedStrategy({2: SignalDirection.BUY}), slippage_pips=0.5)
    trade = res.trades[0]
    assert trade["exit_reason"] == "tp"
    # Entrada 1.10005 (slippage), TP a +10 pips de la entrada, salida con slippage: 10 - 0.5 pips
    assert trade["entry_price"] == pytest.approx(1.10005)
    assert trade["pips"] == pytest.approx(9.5)
    assert trade["exit_price"] == pytest.approx(trade["take_profit"] - 0.5 * PIP)


def test_commission_per_lot_is_charged():
    df = make_df(flat(n=6))
    res = run(df, ScriptedStrategy({2: SignalDirection.BUY}), commission_per_lot=7.0)
    trade = res.trades[0]
    expected = trade["units"] / 100_000 * 7.0
    assert trade["commission"] == pytest.approx(expected, abs=0.01)
    assert trade["pnl"] == pytest.approx(-expected, abs=0.01)


def test_entry_is_at_next_bar_open():
    # La señal sale con la vela 2 cerrada a 1.1000; la vela 3 abre con gap en 1.1050.
    bars = flat(n=3) + [(1.1050, 1.1052, 1.1048, 1.1050)] + flat(1.1050, 2)
    df = make_df(bars)
    res = run(df, ScriptedStrategy({2: SignalDirection.BUY}), spread_pips=1.0)
    trade = res.trades[0]
    assert trade["signal_time"] == df.index[2]
    assert trade["entry_time"] == df.index[3]
    assert trade["entry_price"] == pytest.approx(1.1050 + 0.5 * PIP)
    # SL/TP mantienen la distancia de la estrategia medida desde la entrada real
    assert trade["stop_loss"] == pytest.approx(trade["entry_price"] - 10 * PIP)


def test_signal_on_last_bar_is_not_executed():
    df = make_df(flat(n=4))
    res = run(df, ScriptedStrategy({3: SignalDirection.BUY}))
    assert res.trades == []


def test_stop_loss_wins_when_both_hit_in_same_bar():
    # Una vela de 40 pips de rango toca SL (-10) y TP (+10).
    bars = flat(n=3) + [(1.1000, 1.1020, 1.0980, 1.1000)] + flat(n=2)
    for direction in (SignalDirection.BUY, SignalDirection.SELL):
        trade = run(make_df(bars), ScriptedStrategy({2: direction})).trades[0]
        assert trade["exit_reason"] == "sl"
        assert trade["pips"] == pytest.approx(-10.0)


def test_stop_loss_gap_fills_at_open():
    # Entrada en la vela 3; la vela 4 abre 30 pips abajo (20 por debajo del SL):
    # se llena a la apertura, no al SL.
    bars = flat(n=4) + [(1.0970, 1.0975, 1.0960, 1.0965)] + flat(1.0965, 2)
    trade = run(make_df(bars), ScriptedStrategy({2: SignalDirection.BUY})).trades[0]
    assert trade["exit_reason"] == "sl"
    assert trade["pips"] == pytest.approx(-30.0)


def test_daily_loss_limit_blocks_new_trades_until_next_day():
    # Cada hora: abre en 1.1000 y cae 20 pips -> cada BUY pierde ~1% (SL de 10 pips).
    falling = [(1.1000, 1.1000, 1.0980, 1.0990)] * 30
    df = make_df(flat(n=3) + falling, start="2024-01-02 00:00")
    strat = ScriptedStrategy({}, every_bar=SignalDirection.BUY)

    unlimited = run(df, strat)
    limited = run(df, strat, max_daily_loss_pct=2.5)

    day1 = df.index[0].date()
    n_unlimited = sum(1 for t in unlimited.trades if t["entry_time"].date() == day1)
    n_limited = sum(1 for t in limited.trades if t["entry_time"].date() == day1)
    assert n_unlimited > 10
    # 3 pérdidas de 1% superan el 2.5%: no se abre ninguna más ese día
    assert n_limited == 3
    # Al día siguiente vuelve a operar
    assert any(t["entry_time"].date() > day1 for t in limited.trades)
