"""
Estrategia basada en el modelo predictivo (LightGBM, target de triple barrera).

Funciona igual en el backtester y en el strategy-engine: recibe las velas
cerradas, calcula las features de la última vela y opera solo si la
probabilidad de tocar el TP antes que el SL supera el umbral. El SL y el TP
usan los mismos múltiplos de ATR con los que se construyó el target.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from indicators import calculate_atr
from ml.features import WARMUP_BARS, build_features
from ml.labels import triple_barrier_labels
from ml.model import TradeModel
from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy


class MLStrategy(BaseStrategy):
    def __init__(
        self,
        model: TradeModel,
        threshold: float = 0.5,
        sl_atr: float = 2.0,
        tp_atr: float = 3.0,
        atr_period: int = 14,
        horizon: int = 48,
        pair: str = "EUR_USD",
        spread_pips: float = 0.0,
        slippage_pips: float = 0.0,
    ):
        super().__init__(name="ML_LightGBM")
        self.model = model
        self.threshold = threshold
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.atr_period = atr_period
        self.horizon = horizon
        # Para reconstruir el target igual que en el entrenamiento (diagnostics)
        self.pair = pair
        self.spread_pips = spread_pips
        self.slippage_pips = slippage_pips
        self._cache: Optional[pd.DataFrame] = None

    @classmethod
    def from_config(cls, model: TradeModel, cfg: Dict[str, Any], pair: str) -> "MLStrategy":
        return cls(
            model=model,
            threshold=float(cfg["modelo"]["umbral_probabilidad"]),
            sl_atr=float(cfg["riesgo"]["sl_atr"]),
            tp_atr=float(cfg["riesgo"]["tp_atr"]),
            atr_period=int(cfg["riesgo"]["atr_periodo"]),
            horizon=int(cfg["modelo"]["horizonte_velas"]),
            pair=pair,
            spread_pips=float(cfg["costes"]["spread_pips"]),
            slippage_pips=float(cfg["costes"]["slippage_pips"]),
        )

    @classmethod
    def train(cls, train_df: pd.DataFrame, cfg: Dict[str, Any], pair: str) -> "MLStrategy":
        """Entrena con las velas de `train_df` (solo pasado) y devuelve la estrategia lista."""
        r, m, c = cfg["riesgo"], cfg["modelo"], cfg["costes"]
        features = build_features(train_df, int(r["atr_periodo"]))
        atr = calculate_atr(train_df["high"], train_df["low"], train_df["close"], int(r["atr_periodo"]))
        labels = triple_barrier_labels(
            train_df, atr, int(m["horizonte_velas"]), float(r["sl_atr"]), float(r["tp_atr"]), pair,
            float(c["spread_pips"]), float(c["slippage_pips"]),
        )
        model = TradeModel(m.get("lightgbm"), seed=int(m.get("semilla", 42))).fit(features, labels)
        return cls.from_config(model, cfg, pair)

    @property
    def min_candles_required(self) -> int:
        return WARMUP_BARS

    @property
    def lookback_window(self) -> int:
        # Historia que recibe evaluate(): de sobra para que la EMA 200 converja.
        return 1000

    def precompute(self, df: pd.DataFrame) -> "MLStrategy":
        """
        Para backtests largos: calcula de una vez las probabilidades de todas
        las velas de `df`. Las features son causales, así que el valor de cada
        vela es el mismo que se obtendría en vivo con la historia hasta ella.
        """
        self._cache = self.model.predict_proba(build_features(df, self.atr_period))
        return self

    def probabilities(self, df: pd.DataFrame) -> pd.Series:
        ts = df.index[-1]
        if self._cache is not None and ts in self._cache.index:
            return self._cache.loc[ts]
        return self.model.predict_proba(build_features(df, self.atr_period).iloc[[-1]]).iloc[0]

    def evaluate(self, df: pd.DataFrame, pair: str) -> Optional[Signal]:
        if not self.validate_dataframe(df):
            return None
        p = self.probabilities(df)
        p_long, p_short = float(p["p_long"]), float(p["p_short"])
        if np.isnan(p_long) or np.isnan(p_short):
            return None

        if p_long >= self.threshold and p_long >= p_short:
            direction, sign, prob = SignalDirection.BUY, 1, p_long
        elif p_short >= self.threshold:
            direction, sign, prob = SignalDirection.SELL, -1, p_short
        else:
            return None

        atr = float(calculate_atr(df["high"], df["low"], df["close"], self.atr_period).iloc[-1])
        if not atr > 0:
            return None
        close = float(df["close"].iloc[-1])
        ts = df.index[-1] if isinstance(df.index[-1], datetime) else datetime.now(timezone.utc)
        return Signal(
            pair=pair,
            strategy_name=self.name,
            direction=direction,
            confidence=round(prob, 4),
            timestamp=ts,
            entry_price=close,
            stop_loss=round(close - sign * self.sl_atr * atr, 5),
            take_profit=round(close + sign * self.tp_atr * atr, 5),
            metadata={"p_long": round(p_long, 4), "p_short": round(p_short, 4), "atr": atr,
                      "umbral": self.threshold, "horizonte_velas": self.horizon},
        )

    def diagnostics(self, df: pd.DataFrame, test_start: pd.Timestamp) -> Dict[str, Any]:
        """
        Calidad del clasificador en las velas de prueba (desde `test_start`):
        precisión de las señales frente a la tasa base (lo que acertaría operar
        siempre) y accuracy del clasificador con corte 0.5.
        """
        atr = calculate_atr(df["high"], df["low"], df["close"], self.atr_period)
        labels = triple_barrier_labels(df, atr, self.horizon, self.sl_atr, self.tp_atr, self.pair,
                                       self.spread_pips, self.slippage_pips)
        proba = self._cache if self._cache is not None else self.model.predict_proba(build_features(df, self.atr_period))
        data = proba.join(labels).loc[test_start:].dropna()
        out: Dict[str, Any] = {"muestras": len(data)}
        for side in ("long", "short"):
            p, y = data[f"p_{side}"], data[side]
            chosen = p >= self.threshold
            out[f"tasa_base_{side}"] = float(y.mean()) if len(y) else float("nan")
            out[f"precision_{side}"] = float(y[chosen].mean()) if chosen.any() else float("nan")
            out[f"senales_{side}"] = int(chosen.sum())
            out[f"accuracy_{side}"] = float(((p >= 0.5) == (y == 1)).mean()) if len(y) else float("nan")
            # Lo que acierta un clasificador trivial que siempre predice la clase mayoritaria
            out[f"accuracy_trivial_{side}"] = float(max(y.mean(), 1 - y.mean())) if len(y) else float("nan")
        return out
