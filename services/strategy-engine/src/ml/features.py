"""
Features del modelo. Regla: la fila t usa SOLO velas hasta t inclusive (la
vela t ya cerró cuando se decide). Todo es causal: diferencias hacia atrás,
ventanas móviles e indicadores exponenciales. No hay normalización con
estadísticas globales: las escalas salen del propio pasado (ATR, volatilidad).
"""
import numpy as np
import pandas as pd

from indicators import calculate_atr, calculate_ema, calculate_rsi

# Velas iniciales en las que la EMA 200 y las ventanas largas aún no son fiables
WARMUP_BARS = 250

FEATURE_COLUMNS = [
    "ret_1", "ret_3", "ret_6", "ret_12", "ret_24",
    "atr_pct", "rsi_14",
    "dist_ema_20", "dist_ema_50", "dist_ema_200",
    "vol_24", "vol_120", "vol_ratio",
    "range_atr", "body_atr", "pos_rango_24",
    "hora_sin", "hora_cos", "dia_semana",
]


def build_features(df: pd.DataFrame, atr_period: int = 14) -> pd.DataFrame:
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    f = pd.DataFrame(index=df.index)

    for k in (1, 3, 6, 12, 24):
        f[f"ret_{k}"] = np.log(c / c.shift(k))

    atr = calculate_atr(h, l, c, atr_period)
    f["atr_pct"] = atr / c
    f["rsi_14"] = calculate_rsi(c, 14)
    for p in (20, 50, 200):
        f[f"dist_ema_{p}"] = (c - calculate_ema(c, p)) / atr

    logret = np.log(c).diff()
    f["vol_24"] = logret.rolling(24).std()
    f["vol_120"] = logret.rolling(120).std()
    f["vol_ratio"] = f["vol_24"] / f["vol_120"]

    f["range_atr"] = (h - l) / atr
    f["body_atr"] = (c - o) / atr
    lo24, hi24 = l.rolling(24).min(), h.rolling(24).max()
    f["pos_rango_24"] = (c - lo24) / (hi24 - lo24)

    hour = df.index.hour
    f["hora_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hora_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dia_semana"] = df.index.dayofweek

    return f[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
