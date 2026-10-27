"""
Modelo de trading: dos clasificadores LightGBM sobre las mismas features.
  - p_long:  probabilidad de que un BUY toque el TP antes que el SL.
  - p_short: lo mismo para un SELL.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import joblib
import pandas as pd
from lightgbm import LGBMClassifier

from ml.features import feature_set


class TradeModel:
    def __init__(self, lgbm_params: Optional[Dict[str, Any]] = None, seed: int = 42, conjunto: str = "base"):
        params = dict(lgbm_params or {})
        params.update(random_state=seed, verbose=-1)
        self.params = params
        self.conjunto = conjunto
        self.feature_columns, self.warmup_bars = feature_set(conjunto)
        self.long = LGBMClassifier(**params)
        self.short = LGBMClassifier(**params)
        self.meta: Dict[str, Any] = {}

    def fit(self, features: pd.DataFrame, labels: pd.DataFrame) -> "TradeModel":
        data = features.iloc[self.warmup_bars:].join(labels).dropna()
        if len(data) < 500:
            raise ValueError(f"Muy pocas muestras para entrenar ({len(data)})")
        X = data[self.feature_columns]
        self.long.fit(X, data["long"].astype(int))
        self.short.fit(X, data["short"].astype(int))
        self.meta = {
            "entrenado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "muestras": len(data),
            "desde": str(data.index[0]),
            "hasta": str(data.index[-1]),
            "tasa_base_long": float(data["long"].mean()),
            "tasa_base_short": float(data["short"].mean()),
            "conjunto_features": self.conjunto,
            "features": self.feature_columns,
            "lightgbm": self.params,
        }
        return self

    def predict_proba(self, features: pd.DataFrame) -> pd.DataFrame:
        X = features[self.feature_columns]
        return pd.DataFrame(
            {"p_long": self.long.predict_proba(X)[:, 1], "p_short": self.short.predict_proba(X)[:, 1]},
            index=features.index,
        )

    def save(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, path)

    @staticmethod
    def load(path: Path) -> "TradeModel":
        return joblib.load(path)
