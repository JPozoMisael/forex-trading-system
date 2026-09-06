"""
Pruebas unitarias para los algoritmos y estrategias de trading.
"""
import pandas as pd
import numpy as np
import pytest

from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from shared.models import SignalDirection


def test_ma_crossover_strategy_min_candles():
    strategy = MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=100, use_trend_filter=True)
    assert strategy.min_candles_required >= 105


def test_ma_crossover_strategy_evaluation(sample_ohlcv_dataframe):
    strategy = MACrossoverStrategy(fast_period=5, slow_period=10, trend_period=30, use_trend_filter=False)
    sig = strategy.evaluate(sample_ohlcv_dataframe, pair="EUR_USD")
    # Puede ser None o Signal, pero si retorna Signal debe tener estructura válida
    if sig is not None:
        assert sig.pair == "EUR_USD"
        assert sig.direction in (SignalDirection.BUY, SignalDirection.SELL)
        assert sig.stop_loss is not None
        assert sig.take_profit is not None
        if sig.direction == SignalDirection.BUY:
            assert sig.stop_loss < sig.entry_price < sig.take_profit
        else:
            assert sig.take_profit < sig.entry_price < sig.stop_loss


def test_mean_reversion_strategy_evaluation(sample_ohlcv_dataframe):
    strategy = MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14)
    sig = strategy.evaluate(sample_ohlcv_dataframe, pair="EUR_USD")
    if sig is not None:
        assert sig.pair == "EUR_USD"
        assert sig.direction in (SignalDirection.BUY, SignalDirection.SELL)
        assert sig.stop_loss is not None
        assert sig.take_profit is not None


def test_strategy_insufficient_data():
    strategy = MACrossoverStrategy()
    short_df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05]})
    sig = strategy.evaluate(short_df, pair="EUR_USD")
    assert sig is None
