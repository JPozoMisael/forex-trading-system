"""
Pruebas unitarias para los algoritmos y estrategias de trading.
"""
import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from strategies.ma_crossover import MACrossoverStrategy
from strategies.mean_reversion import MeanReversionStrategy
from shared.models import SignalDirection


def scan_signals(strategy, df, pair="EUR_USD"):
    """Evalúa la estrategia vela a vela (como en vivo) y devuelve todas las señales."""
    signals = []
    for i in range(strategy.min_candles_required, len(df)):
        window = df.iloc[max(0, i + 1 - strategy.lookback_window): i + 1]
        sig = strategy.evaluate(window, pair=pair)
        if sig:
            signals.append(sig)
    return signals


@pytest.fixture(scope="module")
def market_df():
    return generate_synthetic_forex_data(pair="EUR_USD", n_candles=1500, seed=7)


def test_ma_crossover_strategy_min_candles():
    strategy = MACrossoverStrategy(fast_period=9, slow_period=21, trend_period=100, use_trend_filter=True)
    assert strategy.min_candles_required >= 105
    assert strategy.lookback_window >= strategy.min_candles_required


def test_ma_crossover_signals_are_consistent(market_df):
    strategy = MACrossoverStrategy(fast_period=5, slow_period=15, trend_period=50, use_trend_filter=False)
    signals = scan_signals(strategy, market_df)
    assert signals, "la serie de prueba debería producir cruces"
    assert {s.direction for s in signals} == {SignalDirection.BUY, SignalDirection.SELL}
    for s in signals:
        if s.direction == SignalDirection.BUY:
            assert s.stop_loss < s.entry_price < s.take_profit
        else:
            assert s.take_profit < s.entry_price < s.stop_loss


def test_ma_crossover_detects_exact_cross():
    # Caída sostenida y luego subida: el cruce alcista debe aparecer una sola vez
    prices = [1.10 - 0.0005 * i for i in range(40)] + [1.08 + 0.0008 * i for i in range(30)]
    df = pd.DataFrame({"open": prices, "high": [p + 0.0003 for p in prices],
                       "low": [p - 0.0003 for p in prices], "close": prices})
    strategy = MACrossoverStrategy(fast_period=5, slow_period=15, use_trend_filter=False)
    signals = scan_signals(strategy, df)
    assert len(signals) == 1 and signals[0].direction == SignalDirection.BUY


def test_mean_reversion_targets_the_mean(market_df):
    strategy = MeanReversionStrategy(bb_period=20, bb_std=2.0, rsi_period=14)
    signals = scan_signals(strategy, market_df)
    assert signals, "la serie de prueba debería tocar las bandas"
    for s in signals:
        assert s.take_profit == pytest.approx(s.metadata["bb_mid"], abs=1e-5)
        if s.direction == SignalDirection.BUY:
            assert s.stop_loss < s.entry_price < s.take_profit
        else:
            assert s.take_profit < s.entry_price < s.stop_loss


def test_strategy_insufficient_data():
    strategy = MACrossoverStrategy()
    short_df = pd.DataFrame({"open": [1.0], "high": [1.1], "low": [0.9], "close": [1.05]})
    assert strategy.evaluate(short_df, pair="EUR_USD") is None
