"""
Cálculo de indicadores técnicos cuantitativos utilizando Pandas y NumPy.
Implementaciones matemáticas estándar optimizadas para series de tiempo.
"""
from typing import Tuple
import numpy as np
import pandas as pd


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Media Móvil Simple (SMA)"""
    return series.rolling(window=period, min_periods=period).mean()


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Media Móvil Exponencial (EMA)"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Índice de Fuerza Relativa (RSI) de Wilder.
    Rango: 0 a 100.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's Exponential Smoothing
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50.0)


def calculate_macd(
    series: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Moving Average Convergence Divergence (MACD).
    Retorna: (macd_line, signal_line, histogram)
    """
    ema_fast = calculate_ema(series, fast_period)
    ema_slow = calculate_ema(series, slow_period)
    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal_period)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """
    Bandas de Bollinger.
    Retorna: (upper_band, middle_band, lower_band)
    """
    middle_band = calculate_sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper_band = middle_band + (std * num_std)
    lower_band = middle_band - (std * num_std)
    return upper_band, middle_band, lower_band


def calculate_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """
    Average True Range (ATR) de Wilder para medir la volatilidad del mercado.
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


def calculate_supertrend(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 10,
    multiplier: float = 3.0,
) -> Tuple[pd.Series, pd.Series]:
    """
    Indicador SuperTrend para detección de tendencia y trailing stop.
    Retorna: (supertrend_line, direction) donde direction es 1 (alcista) o -1 (bajista).
    """
    atr = calculate_atr(high, low, close, period=period)
    hl2 = (high + low) / 2
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)

    n = len(close)
    st = np.zeros(n)
    direction = np.ones(n)

    for i in range(1, n):
        if close.iloc[i] > upper_band.iloc[i - 1]:
            direction[i] = 1
        elif close.iloc[i] < lower_band.iloc[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]

        if direction[i] == 1:
            st[i] = max(lower_band.iloc[i], st[i - 1]) if direction[i - 1] == 1 else lower_band.iloc[i]
        else:
            st[i] = min(upper_band.iloc[i], st[i - 1]) if direction[i - 1] == -1 else upper_band.iloc[i]

    return pd.Series(st, index=close.index), pd.Series(direction, index=close.index)


def calculate_stochastic(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    k_period: int = 14,
    d_period: int = 3,
) -> Tuple[pd.Series, pd.Series]:
    """
    Oscilador Estocástico (%K y %D).
    """
    lowest_low = low.rolling(window=k_period).min()
    highest_high = high.rolling(window=k_period).max()
    denom = (highest_high - lowest_low).replace(0, np.nan)
    k = 100 * ((close - lowest_low) / denom)
    d = k.rolling(window=d_period).mean()
    return k.fillna(50.0), d.fillna(50.0)
