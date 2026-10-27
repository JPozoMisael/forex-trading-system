"""
El modelo en producción (strategy-engine y pipeline.py).

`load_or_train` carga models/ml_<PAR>_<TF>.joblib y, si no existe (por
ejemplo, en un contenedor recién desplegado), lo entrena con la historia de
Dukascopy, igual que `pipeline.py entrenar`, y lo guarda para el próximo
arranque.
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ml.model import TradeModel
from shared.dukascopy import fetch_mid_candles, only_closed
from strategies.ml_strategy import MLStrategy


def model_path(models_dir: Path, pair: str, gran: str) -> Path:
    return Path(models_dir) / f"ml_{pair}_{gran}.joblib"


def save_model(strategy: MLStrategy, path: Path, cfg: Dict[str, Any], extra: Optional[Dict[str, Any]] = None):
    """Guarda el modelo y, al lado, un .json con sus metadatos y la config usada."""
    strategy.model.save(path)
    m = cfg["mercado"]
    meta = dict(strategy.model.meta)
    meta.update(par=m["par"], temporalidad=m["temporalidad"], config=cfg)
    meta.update(extra or {})
    path.with_suffix(".json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return meta


def load_or_train(
    cfg: Dict[str, Any],
    models_dir: Path,
    log: Callable[[str], None] = print,
    fetch=fetch_mid_candles,
    now: Optional[datetime] = None,
) -> MLStrategy:
    m = cfg["mercado"]
    pair, gran = m["par"], m["temporalidad"]
    path = model_path(models_dir, pair, gran)
    if path.exists():
        strategy = MLStrategy.from_config(TradeModel.load(path), cfg, pair)
        log(f"Modelo cargado de {path} (entrenado hasta {strategy.model.meta.get('hasta', '?')[:16]})")
        return strategy

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=365.25 * float(m["anios_historia"]))
    log(f"No hay modelo en {path}: se entrena con {m['anios_historia']} años de {pair} {gran} de Dukascopy...")
    df = fetch(pair, gran, start, now)
    df = only_closed(df, gran, now)
    if df.empty:
        raise RuntimeError(f"Dukascopy no devolvió velas de {pair} {gran}; no se puede entrenar el modelo")

    strategy = MLStrategy.train(df, cfg, pair)
    meta = save_model(strategy, path, cfg)
    log(f"Modelo entrenado con {meta['muestras']:,} muestras ({meta['desde'][:10]} a {meta['hasta'][:10]}) -> {path}")
    return strategy
