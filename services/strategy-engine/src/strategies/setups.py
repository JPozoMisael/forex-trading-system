"""
Setups de trader al estilo TradingView, PRE-REGISTRADOS en config.yaml (sección
`setups`, 2026-11-03). Las reglas están escritas ahí; este módulo las traduce
sin agregar nada. Compras descritas; las ventas son el espejo exacto.

Cada setup marca, con la vela H1 CERRADA, si hay entrada y dónde van el SL y el
TP. Las tendencias de H4 y diario usan solo velas mayores ya cerradas
(ml/tradingview.py). Funciona igual en vivo (calcula con las velas que recibe)
y en backtest (precompute: calcula todo una vez; es causal, da lo mismo).
"""
from typing import Dict, Optional

import numpy as np
import pandas as pd

from indicators import calculate_atr, calculate_bollinger_bands, calculate_ema, calculate_rsi
from ml.tradingview import (FIB_BARS, TV_WARMUP_BARS, candle_patterns, fibonacci, higher_tf_emas, trading_day,
                            trend)
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy

SETUP_NAMES = ("fibonacci", "ema50", "ruptura_londres", "bollinger_rsi")
BUFFER_ATR = 0.25      # margen del SL más allá del nivel, en ATR
MIN_RR = 1.5           # objetivo mínimo / riesgo (el mismo que exige el risk-manager)


def _context(df: pd.DataFrame) -> pd.DataFrame:
    """Lo que usan los cuatro setups, todo con velas cerradas hasta cada fila."""
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    x = pd.DataFrame(index=df.index)
    x["atr"] = calculate_atr(h, l, c, 14)
    x["ema50"] = calculate_ema(c, 50)
    x["tend_h1"] = trend(c, x["ema50"], calculate_ema(c, 200))
    for col, rule in (("tend_h4", "4h"), ("tend_d", "1D")):
        e = higher_tf_emas(df, rule)
        x[col] = trend(c, e["ema50"], e["ema200"]).where(e["ema200"].notna())
    # Fibonacci del último movimiento de FIB_BARS velas (como el gráfico del reporte)
    x["hi"], x["lo"] = h.rolling(FIB_BARS).max(), l.rolling(FIB_BARS).min()
    x["fib_up"] = fibonacci(df, x["atr"], FIB_BARS)["fib_mov"]  # +1 si el máximo es posterior al mínimo
    x = x.join(candle_patterns(df))
    x["min5"], x["max5"] = l.rolling(5).min(), h.rolling(5).max()
    upper, _, lower = calculate_bollinger_bands(c, 20, 2.0)
    x["bb_up"], x["bb_low"] = upper, lower
    x["rsi"] = calculate_rsi(c, 14)
    day = trading_day(df.index)  # el domingo cuenta como lunes: "ayer" el lunes es el viernes
    daily = pd.DataFrame({"max": h.groupby(day).max(), "min": l.groupby(day).min()}).shift(1)
    x["ayer_max"] = daily["max"].reindex(day).to_numpy()
    x["ayer_min"] = daily["min"].reindex(day).to_numpy()
    x["hora_londres"] = df.index.tz_convert("Europe/London").hour
    return x


def setup_signals(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """
    Para cada vela: dirección (+1 compra, -1 venta, 0 nada), SL y TP del setup
    `name`. Precio de referencia = cierre de la vela (el motor conserva las
    distancias desde el precio real de entrada).
    """
    if name not in SETUP_NAMES:
        raise ValueError(f"Setup desconocido '{name}'. Opciones: {SETUP_NAMES}")
    x = _context(df)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    atr, buf = x["atr"], BUFFER_ATR * x["atr"]
    out = pd.DataFrame({"dir": 0.0, "sl": np.nan, "tp": np.nan}, index=df.index)

    def put(mask, d, sl, tp):
        mask = mask.fillna(False).astype(bool) & (out["dir"] == 0)
        out.loc[mask, "dir"], out.loc[mask, "sl"], out.loc[mask, "tp"] = d, sl[mask], tp[mask]

    if name == "fibonacci":
        rng = x["hi"] - x["lo"]
        for d in (1, -1):
            extreme, origin = (x["hi"], x["lo"]) if d > 0 else (x["lo"], x["hi"])
            retro = (extreme - c) / rng * d                     # 0 = en el extremo, 1 = en el origen
            trigger = (x["vela_envolvente"] == d) | (x["vela_martillo"] == d)
            sl = origin - d * buf
            tp = extreme + d * 0.272 * rng
            ok = ((x["tend_d"] == d) & (x["tend_h4"] == d) & (x["fib_up"] == d) & retro.between(0.5, 0.618)
                  & trigger & ((tp - c) * d >= MIN_RR * (c - sl) * d) & ((c - sl) * d > 0))
            put(ok, d, sl, tp)

    elif name == "ema50":
        for d in (1, -1):
            touches = (l <= x["ema50"]) if d > 0 else (h >= x["ema50"])
            closes = ((c > x["ema50"]) & (c > o)) if d > 0 else ((c < x["ema50"]) & (c < o))
            sl = (x["min5"] if d > 0 else x["max5"]) - d * buf
            risk = (c - sl) * d
            ok = (x["tend_d"] == d) & (x["tend_h4"] == d) & (x["tend_h1"] == d) & touches & closes & (risk > 0)
            put(ok, d, sl, c + d * 2 * risk)

    elif name == "ruptura_londres":
        first_hours = x["hora_londres"].isin([8, 9, 10])
        for d in (1, -1):
            level = x["ayer_max"] if d > 0 else x["ayer_min"]
            cross = ((c - level) * d > 0) & ((c.shift(1) - level) * d <= 0)
            sl = (l if d > 0 else h) - d * buf
            risk = (c - sl) * d
            tp = c + d * 2 * risk
            ok = first_hours & cross & (x["tend_d"] != -d) & (risk > 0) & (risk <= 2 * atr)
            put(ok, d, sl, tp)

    elif name == "bollinger_rsi":
        for d in (1, -1):
            band = x["bb_low"] if d > 0 else x["bb_up"]
            outside_before = ((c.shift(1) - band.shift(1)) * d < 0) & \
                ((x["rsi"].shift(1) < 30) if d > 0 else (x["rsi"].shift(1) > 70))
            back_inside = (c - band) * d > 0
            sl = (x["min5"] if d > 0 else x["max5"]) - d * buf
            risk = (c - sl) * d
            ok = (x["tend_d"] == 0) & outside_before & back_inside & (risk > 0)
            put(ok, d, sl, c + d * MIN_RR * risk)

    return out


class SetupStrategy(BaseStrategy):
    def __init__(self, setup: str):
        if setup not in SETUP_NAMES:
            raise ValueError(f"Setup desconocido '{setup}'. Opciones: {SETUP_NAMES}")
        super().__init__(name=f"Setup_{setup}")
        self.setup = setup
        self._cache: Optional[pd.DataFrame] = None

    @property
    def min_candles_required(self) -> int:
        return TV_WARMUP_BARS

    @property
    def lookback_window(self) -> int:
        return TV_WARMUP_BARS + 500

    def precompute(self, df: pd.DataFrame) -> "SetupStrategy":
        """Para backtests largos: señales de todas las velas de una vez (son causales)."""
        self._cache = setup_signals(df, self.setup)
        return self

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        if not self.validate_dataframe(df):
            return None
        ts = df.index[-1]
        if self._cache is not None and ts in self._cache.index:
            row = self._cache.loc[ts]
        else:
            row = setup_signals(df, self.setup).iloc[-1]
        if row["dir"] == 0 or pd.isna(row["sl"]) or pd.isna(row["tp"]):
            return None
        direction = SignalDirection.BUY if row["dir"] > 0 else SignalDirection.SELL
        return Signal(pair=pair, strategy_name=self.name, direction=direction, confidence=1.0, timestamp=ts,
                      entry_price=float(df["close"].iloc[-1]), stop_loss=float(row["sl"]),
                      take_profit=float(row["tp"]), metadata={"setup": self.setup})


def setup_factory(name: str, signals: Dict[str, pd.DataFrame], key: str):
    """Para validation.validate: reglas fijas, no entrena; reutiliza las señales precalculadas."""
    def factory(train: pd.DataFrame, w) -> SetupStrategy:
        s = SetupStrategy(name)
        s._cache = signals[key]
        return s
    return factory
