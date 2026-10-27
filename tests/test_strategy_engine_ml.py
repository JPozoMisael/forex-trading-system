"""
El modelo dentro del strategy-engine desplegado: se entrena solo si no hay
modelo guardado, se reutiliza tras un reinicio, y solo opera su par y
temporalidad.
"""
import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from ml import serving
from strategies.ma_crossover import MACrossoverStrategy
from strategies.ml_strategy import MLStrategy

ROOT = Path(__file__).resolve().parent.parent

CFG = {
    "mercado": {"par": "EUR_USD", "temporalidad": "H1", "anios_historia": 50},
    "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
    "riesgo": {"atr_periodo": 14, "sl_atr": 2.0, "tp_atr": 3.0},
    "modelo": {"horizonte_velas": 24, "umbral_probabilidad": 0.5, "semilla": 1,
               "lightgbm": {"n_estimators": 30, "num_leaves": 7, "min_child_samples": 50}},
}


@pytest.fixture(scope="module")
def candles():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=3000, granularity_minutes=60, seed=5)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df


@pytest.fixture
def fake_fetch(candles):
    calls = []

    def fetch(pair, gran, start, end):
        calls.append((pair, gran))
        return candles[(candles.index >= start) & (candles.index < end)]

    fetch.calls = calls
    return fetch


def test_trains_saves_and_reuses_model(candles, fake_fetch, tmp_path):
    now = candles.index[-1] + pd.Timedelta(hours=2)
    first = serving.load_or_train(CFG, tmp_path, log=lambda m: None, fetch=fake_fetch, now=now)
    path = serving.model_path(tmp_path, "EUR_USD", "H1")
    assert path.exists() and path.with_suffix(".json").exists()
    assert len(fake_fetch.calls) == 1

    # Reinicio del servicio: carga el archivo, sin volver a descargar ni entrenar
    second = serving.load_or_train(CFG, tmp_path, log=lambda m: None, fetch=fake_fetch, now=now)
    assert len(fake_fetch.calls) == 1
    window = candles.iloc[-1000:]
    pd.testing.assert_series_equal(first.probabilities(window), second.probabilities(window))


def test_no_data_fails_instead_of_running_without_model(tmp_path):
    empty = lambda pair, gran, start, end: pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                                                        index=pd.DatetimeIndex([], tz="UTC"))
    with pytest.raises(RuntimeError):
        serving.load_or_train(CFG, tmp_path, log=lambda m: None, fetch=empty)


def test_ml_strategy_only_supports_its_pair_and_timeframe(candles):
    strat = MLStrategy.train(candles, CFG, "EUR_USD")
    assert strat.supports("EUR_USD", "H1")
    assert strat.supports("EUR_USD", "h1")
    assert not strat.supports("GBP_USD", "H1")
    assert not strat.supports("EUR_USD", "M5")
    assert MACrossoverStrategy().supports("GBP_USD", "M5")


def _load_strategy_engine_main():
    spec = importlib.util.spec_from_file_location(
        "strategy_engine_main", ROOT / "services" / "strategy-engine" / "src" / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_strategy_engine_builds_active_strategies(candles, fake_fetch, tmp_path):
    engine = _load_strategy_engine_main()
    assert [s.name for s in engine.build_strategies(["ma_crossover", "mean_reversion"], tmp_path)] == \
        [engine.MACrossoverStrategy().name, engine.MeanReversionStrategy().name]
    with pytest.raises(ValueError):
        engine.build_strategies(["inventada"], tmp_path)
    with pytest.raises(ValueError):
        engine.build_strategies([], tmp_path)

    # "ml" carga el modelo guardado (aquí uno pequeño entrenado con datos sintéticos)
    serving.load_or_train(CFG, tmp_path, log=lambda m: None, fetch=fake_fetch,
                          now=candles.index[-1] + pd.Timedelta(hours=2))
    (ml,) = engine.build_strategies(["ml"], tmp_path)
    assert ml.name == "ML_LightGBM" and ml.supports("EUR_USD", "H1")
