"""
Pruebas unitarias para el motor de Backtesting y métricas cuantitativas.
"""
import pandas as pd
import numpy as np
import pytest

from engine import BacktestEngine
from metrics import calculate_metrics
from strategies.ma_crossover import MACrossoverStrategy
from data_loader import generate_synthetic_forex_data


def test_metrics_calculation():
    trades = [
        {"pnl": 150.0, "pips": 30.0},
        {"pnl": -50.0, "pips": -10.0},
        {"pnl": 200.0, "pips": 40.0},
        {"pnl": -80.0, "pips": -16.0},
    ]
    equity = pd.Series([10000, 10150, 10100, 10300, 10220])
    metrics = calculate_metrics(trades, equity, initial_balance=10000.0)

    assert metrics.total_trades == 4
    assert metrics.winning_trades == 2
    assert metrics.losing_trades == 2
    assert metrics.win_rate_pct == 50.0
    # Gross Profit = 350, Gross Loss = 130 -> Profit Factor ~ 2.69
    assert metrics.profit_factor == pytest.approx(2.69, rel=0.05)
    assert metrics.total_return_pct == pytest.approx(2.2, rel=0.1)


def test_backtest_engine_run():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=500, seed=123)
    strategy = MACrossoverStrategy(fast_period=5, slow_period=15, trend_period=40, use_trend_filter=False)
    engine = BacktestEngine(initial_balance=10000.0, risk_per_trade_pct=1.0)

    result = engine.run(strategy=strategy, df=df, pair="EUR_USD")
    assert result.strategy_name == "EMA_Crossover"
    assert result.pair == "EUR_USD"
    assert not result.equity_series.empty
    assert result.metrics.initial_balance == 10000.0
