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

# Contexto de temporalidades mayores y régimen de volatilidad
EXTENDED_COLUMNS = [
    "ret_48", "ret_120", "ret_240",
    "dist_max_120", "dist_min_120",
    "pendiente_ema_50", "pendiente_ema_200",
    "regimen_atr",
    "pos_rango_dia_anterior", "dist_max_dia_anterior", "dist_min_dia_anterior",
]

FEATURE_SETS = {
    "base": (FEATURE_COLUMNS, WARMUP_BARS),
    "extendido": (FEATURE_COLUMNS + EXTENDED_COLUMNS, 600),
}


def feature_set(name: str):
    """(columnas, velas de calentamiento) del conjunto de features `name`."""
    if name not in FEATURE_SETS:
        raise ValueError(f"Conjunto de features desconocido '{name}'. Opciones: {sorted(FEATURE_SETS)}")
    return FEATURE_SETS[name]


def build_features(df: pd.DataFrame, atr_period: int = 14, conjunto: str = "base") -> pd.DataFrame:
    columns, _ = feature_set(conjunto)
    f = _base_features(df, atr_period)
    if conjunto == "extendido":
        f = f.join(_extended_features(df, atr_period))
    return f[columns].replace([np.inf, -np.inf], np.nan)


def _extended_features(df: pd.DataFrame, atr_period: int) -> pd.DataFrame:
    h, l, c = df["high"], df["low"], df["close"]
    atr = calculate_atr(h, l, c, atr_period)
    f = pd.DataFrame(index=df.index)
    for k in (48, 120, 240):
        f[f"ret_{k}"] = np.log(c / c.shift(k))
    f["dist_max_120"] = (c - h.rolling(120).max()) / atr
    f["dist_min_120"] = (c - l.rolling(120).min()) / atr
    for p in (50, 200):
        ema = calculate_ema(c, p)
        f[f"pendiente_ema_{p}"] = (ema - ema.shift(24)) / atr
    f["regimen_atr"] = atr / atr.rolling(500).mean()

    # Día anterior COMPLETO (UTC): shift(1) sobre días terminados, luego se lleva a cada vela del día siguiente
    day = df.index.floor("D")
    daily = pd.DataFrame({"max": h.groupby(day).max(), "min": l.groupby(day).min()}).shift(1)
    prev_max = pd.Series(daily["max"].reindex(day).to_numpy(), index=df.index)
    prev_min = pd.Series(daily["min"].reindex(day).to_numpy(), index=df.index)
    f["pos_rango_dia_anterior"] = (c - prev_min) / (prev_max - prev_min)
    f["dist_max_dia_anterior"] = (c - prev_max) / atr
    f["dist_min_dia_anterior"] = (c - prev_min) / atr
    return f


def _base_features(df: pd.DataFrame, atr_period: int) -> pd.DataFrame:
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
    return f
