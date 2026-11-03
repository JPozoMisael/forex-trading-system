"""
Herramientas de TradingView convertidas en datos para el modelo y para el
estudio de patrones: tendencia por EMAs en H1/H4/diario, Fibonacci del último
movimiento, RSI, MACD, Bollinger, estocástico, sesiones, máximo y mínimo de
ayer y patrones de vela.

Regla de siempre: la fila t usa SOLO lo que se veía al cerrar la vela t. Las
temporalidades mayores se construyen con las velas H1 y solo se usan cuando su
vela ya cerró (la de 4 h de 8:00 a 12:00 se usa desde la vela H1 de 11:00, que
cierra a las 12:00). Distancias en "velas típicas" (ATR 14), como en el reporte.
"""
from typing import Dict, List

import numpy as np
import pandas as pd

from indicators import (calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_macd, calculate_rsi,
                        calculate_stochastic)

# Velas que se miran para el Fibonacci (igual que el gráfico del reporte: 5 días de H1)
FIB_BARS = 120

# Nombre de cada dato y cómo leerlo (para el informe). Tendencias: +1 alcista, -1 bajista, 0 mixta.
TV_COLUMNS: Dict[str, str] = {
    "tend_h1": "Tendencia H1 (precio, EMA 50 y EMA 200)",
    "tend_h4": "Tendencia H4",
    "tend_d": "Tendencia diaria",
    "alineacion": "Temporalidades a favor (suma de H1 + H4 + diario: +3 todas alcistas, −3 todas bajistas)",
    "fib_mov": "Último movimiento de Fibonacci (+1 subida, −1 bajada)",
    "fib_retroceso": "Cuánto retrocedió el último movimiento (0 = en el extremo, 1 = lo recorrió entero)",
    "fib_dist_nivel": "Distancia al nivel de Fibonacci más cercano (velas típicas)",
    "rsi": "RSI 14",
    "macd_hist": "Histograma MACD (velas típicas)",
    "bb_pctb": "Posición en las Bandas de Bollinger (0 = banda inferior, 1 = superior)",
    "bb_ancho": "Ancho de Bollinger frente a su promedio (< 1 = bandas apretadas)",
    "estocastico": "Estocástico %K",
    "sesion": "Sesión (0 Asia, 1 Londres, 2 Londres + Nueva York, 3 Nueva York)",
    "dist_max_ayer": "Distancia al máximo de ayer (velas típicas; > 0 = por encima)",
    "dist_min_ayer": "Distancia al mínimo de ayer (velas típicas; < 0 = por debajo)",
    "vela_envolvente": "Vela envolvente (+1 alcista, −1 bajista, 0 no)",
    "vela_martillo": "Martillo (+1) o estrella fugaz (−1)",
}

# Datos que se leen por categoría (el resto se agrupa por tramos)
CATEGORICAL = {"tend_h1", "tend_h4", "tend_d", "alineacion", "fib_mov", "sesion", "vela_envolvente", "vela_martillo"}

# Velas iniciales hasta que la EMA 200 del diario tiene algo de historia
TV_WARMUP_BARS = 24 * 220


def trend(close: pd.Series, ema50: pd.Series, ema200: pd.Series) -> pd.Series:
    """La misma regla del reporte: alcista si precio > EMA 50 > EMA 200, bajista al revés, si no mixta."""
    up = (close > ema50) & (ema50 > ema200)
    down = (close < ema50) & (ema50 < ema200)
    return pd.Series(np.where(up, 1.0, np.where(down, -1.0, 0.0)), index=close.index)


def bar_step(index: pd.DatetimeIndex) -> pd.Timedelta:
    """
    Duración de una vela: la separación MÍNIMA entre velas seguidas. La primera
    separación no sirve: si cae sobre un fin de semana o la pausa del oro, sale
    de horas o días y las velas mayores se usarían antes de cerrar.
    """
    if len(index) < 2:
        return pd.Timedelta(hours=1)
    return pd.Series(index).diff().dropna().min()


def trading_day(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """
    Día de trading de cada vela (UTC): las velas del domingo (apertura de la
    semana, 2-3 horas) cuentan como parte del lunes, como el diario de TradingView.
    Así "ayer" el lunes es el viernes y no un trozo de domingo.
    """
    day = index.floor("D")
    return day + pd.to_timedelta((index.dayofweek == 6).astype(int), unit="D")


def higher_tf_emas(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """
    EMA 50 y 200 de una temporalidad mayor (`rule`: '4h', '1D') llevadas a cada
    vela H1, usando solo velas mayores ya cerradas al cierre de la vela H1.
    En el diario, el domingo se une al lunes (trading_day).
    """
    step = bar_step(df.index)
    close = df["close"]
    if rule == "1D":
        close = close.groupby(trading_day(df.index)).last()
        big = close
    else:
        big = close.resample(rule, label="left", closed="left").last().dropna()
    emas = pd.DataFrame({"ema50": calculate_ema(big, 50), "ema200": calculate_ema(big, 200)})
    emas.index = emas.index + pd.Timedelta(rule)            # cuándo cierra cada vela mayor
    h1_close = df.index + step                              # cuándo cierra cada vela H1
    out = emas.reindex(emas.index.union(h1_close)).ffill().reindex(h1_close)
    out.index = df.index
    return out


def _rolling_extreme_position(values: np.ndarray, window: int, fn) -> np.ndarray:
    """Para cada vela, hace cuántas velas estuvo el extremo (`fn` = argmax/argmin) de la ventana."""
    n = len(values)
    out = np.full(n, np.nan)
    if n < window:
        return out
    view = np.lib.stride_tricks.sliding_window_view(values, window)
    out[window - 1:] = window - 1 - fn(view, axis=1)
    return out


def fibonacci(df: pd.DataFrame, atr: pd.Series, bars: int = FIB_BARS) -> pd.DataFrame:
    """
    Fibonacci del último gran movimiento de las últimas `bars` velas, como el
    del reporte: si el máximo es posterior al mínimo, el movimiento fue de subida.
    """
    h, l, c = df["high"], df["low"], df["close"]
    hi, lo = h.rolling(bars).max(), l.rolling(bars).min()
    ago_hi = _rolling_extreme_position(h.to_numpy(dtype=float), bars, np.argmax)
    ago_lo = _rolling_extreme_position(l.to_numpy(dtype=float), bars, np.argmin)
    up = pd.Series(np.where(np.isnan(ago_hi), np.nan, np.where(ago_hi < ago_lo, 1.0, -1.0)), index=df.index)
    rng = (hi - lo).replace(0, np.nan)
    retro = pd.Series(np.where(up > 0, (hi - c) / rng, (c - lo) / rng), index=df.index).where(up.notna())
    ratios = (0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0)
    dist = pd.concat([(retro - r).abs() for r in ratios], axis=1).min(axis=1) * rng / atr
    return pd.DataFrame({"fib_mov": up, "fib_retroceso": retro, "fib_dist_nivel": dist})


def sessions(index: pd.DatetimeIndex) -> pd.Series:
    """0 Asia, 1 Londres, 2 Londres y Nueva York a la vez, 3 Nueva York (8:00-17:00 en cada ciudad)."""
    london = index.tz_convert("Europe/London").hour
    ny = index.tz_convert("America/New_York").hour
    in_l, in_ny = (london >= 8) & (london < 17), (ny >= 8) & (ny < 17)
    return pd.Series(np.select([in_l & in_ny, in_l, in_ny], [2.0, 1.0, 3.0], 0.0), index=index)


def candle_patterns(df: pd.DataFrame) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    body = (c - o).abs()
    po, pc = o.shift(1), c.shift(1)
    bull_engulf = (c > o) & (pc < po) & (c >= po) & (o <= pc)
    bear_engulf = (c < o) & (pc > po) & (c <= po) & (o >= pc)
    upper, lower = h - np.maximum(o, c), np.minimum(o, c) - l
    rng = (h - l).replace(0, np.nan)
    hammer = (lower >= 2 * body) & (upper <= 0.25 * rng)
    star = (upper >= 2 * body) & (lower <= 0.25 * rng)
    return pd.DataFrame({
        "vela_envolvente": np.where(bull_engulf, 1.0, np.where(bear_engulf, -1.0, 0.0)),
        "vela_martillo": np.where(hammer & ~star, 1.0, np.where(star & ~hammer, -1.0, 0.0)),
    }, index=df.index)


def tradingview_features(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    """Un dato por herramienta (ver TV_COLUMNS), solo con velas cerradas hasta t."""
    h, l, c = df["high"], df["low"], df["close"]
    atr = calculate_atr(h, l, c, atr_period)
    f = pd.DataFrame(index=df.index)

    f["tend_h1"] = trend(c, calculate_ema(c, 50), calculate_ema(c, 200))
    for col, rule in (("tend_h4", "4h"), ("tend_d", "1D")):
        e = higher_tf_emas(df, rule)
        f[col] = trend(c, e["ema50"], e["ema200"]).where(e["ema200"].notna())
    f["alineacion"] = f["tend_h1"] + f["tend_h4"] + f["tend_d"]

    f = f.join(fibonacci(df, atr))
    f["rsi"] = calculate_rsi(c, 14)
    f["macd_hist"] = calculate_macd(c)[2] / atr
    upper, middle, lower = calculate_bollinger_bands(c, 20, 2.0)
    width = (upper - lower) / middle
    f["bb_pctb"] = (c - lower) / (upper - lower).replace(0, np.nan)
    f["bb_ancho"] = width / width.rolling(120).mean()
    k, _ = calculate_stochastic(h, l, c)
    f["estocastico"] = k.where(h.rolling(14).count() == 14)
    f["sesion"] = sessions(df.index)

    # Máximo y mínimo del día anterior COMPLETO (UTC; el domingo cuenta como lunes)
    day = trading_day(df.index)
    daily = pd.DataFrame({"max": h.groupby(day).max(), "min": l.groupby(day).min()}).shift(1)
    f["dist_max_ayer"] = (c - daily["max"].reindex(day).to_numpy()) / atr
    f["dist_min_ayer"] = (c - daily["min"].reindex(day).to_numpy()) / atr

    f = f.join(candle_patterns(df))
    return f[list(TV_COLUMNS)].replace([np.inf, -np.inf], np.nan)


def tv_column_names() -> List[str]:
    return list(TV_COLUMNS)
