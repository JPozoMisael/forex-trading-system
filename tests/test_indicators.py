"""
Pruebas unitarias para la biblioteca cuantitativa de indicadores técnicos.
"""
import pandas as pd
import numpy as np
import pytest

from indicators import (
    calculate_sma,
    calculate_ema,
    calculate_rsi,
    calculate_macd,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_supertrend,
    calculate_stochastic,
)


def test_calculate_sma(sample_ohlcv_dataframe):
    close = sample_ohlcv_dataframe["close"]
    sma20 = calculate_sma(close, 20)
    assert len(sma20) == len(close)
    assert pd.isna(sma20.iloc[18])
    assert not pd.isna(sma20.iloc[19])
    assert sma20.iloc[19] == pytest.approx(close.iloc[:20].mean(), rel=1e-5)


def test_calculate_ema(sample_ohlcv_dataframe):
    close = sample_ohlcv_dataframe["close"]
    ema12 = calculate_ema(close, 12)
    assert len(ema12) == len(close)
    assert not pd.isna(ema12.iloc[-1])


def test_calculate_rsi(sample_ohlcv_dataframe):
    close = sample_ohlcv_dataframe["close"]
    rsi = calculate_rsi(close, 14)
    assert len(rsi) == len(close)
    valid_rsi = rsi.dropna()
    assert (valid_rsi >= 0.0).all()
    assert (valid_rsi <= 100.0).all()


def test_calculate_macd(sample_ohlcv_dataframe):
    close = sample_ohlcv_dataframe["close"]
    macd, signal, hist = calculate_macd(close, fast_period=12, slow_period=26, signal_period=9)
    assert len(macd) == len(close)
    assert len(signal) == len(close)
    assert len(hist) == len(close)
    # Histograma debe ser macd - signal
    np.testing.assert_allclose(hist.values, (macd - signal).values, rtol=1e-5)


def test_calculate_bollinger_bands(sample_ohlcv_dataframe):
    close = sample_ohlcv_dataframe["close"]
    upper, mid, lower = calculate_bollinger_bands(close, period=20, num_std=2.0)
    valid_idx = 25
    assert upper.iloc[valid_idx] > mid.iloc[valid_idx]
    assert mid.iloc[valid_idx] > lower.iloc[valid_idx]


def test_calculate_atr(sample_ohlcv_dataframe):
    df = sample_ohlcv_dataframe
    atr = calculate_atr(df["high"], df["low"], df["close"], period=14)
    assert len(atr) == len(df)
    valid_atr = atr.dropna()
    assert (valid_atr >= 0.0).all()


def test_calculate_supertrend(sample_ohlcv_dataframe):
    df = sample_ohlcv_dataframe
    st, direction = calculate_supertrend(df["high"], df["low"], df["close"], period=10, multiplier=3.0)
    assert len(st) == len(df)
    assert len(direction) == len(df)
    assert set(direction.unique()).issubset({-1.0, 1.0})


def test_calculate_stochastic(sample_ohlcv_dataframe):
    df = sample_ohlcv_dataframe
    k, d = calculate_stochastic(df["high"], df["low"], df["close"], k_period=14, d_period=3)
    assert len(k) == len(df)
    assert len(d) == len(df)
    assert (k >= 0.0).all() and (k <= 100.0).all()
