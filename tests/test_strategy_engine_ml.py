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


def test_ml_strategy_records_what_it_saw(candles):
    strat = MLStrategy.train(candles, CFG, "EUR_USD")
    assert strat.last_state is None
    strat.evaluate(candles.iloc[-1000:], "EUR_USD")
    p = strat.probabilities(candles.iloc[-1000:])
    assert strat.last_state == {"p_long": round(float(p["p_long"]), 4), "p_short": round(float(p["p_short"]), 4),
                                "umbral": 0.5}


def test_active_strategies_from_env_are_lowercase(monkeypatch):
    from shared.config import Settings

    monkeypatch.setenv("ACTIVE_STRATEGIES", "ml, MA_Crossover")
    assert Settings().active_strategies == ["ml", "ma_crossover"]
    monkeypatch.delenv("ACTIVE_STRATEGIES")
    assert Settings().active_strategies == ["ml"]


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



def test_one_model_per_active_instrument(candles, fake_fetch, tmp_path, monkeypatch):
    import dataclasses

    from shared.config import instrument_config

    engine = _load_strategy_engine_main()
    monkeypatch.setattr(engine, "settings",
                        dataclasses.replace(engine.settings, forex_pairs=["EUR_USD", "XAU_USD", "GBP_USD"],
                                            candle_granularity="H1"))
    cfg = dict(CFG, produccion={"activos": ["EUR_USD", "XAU_USD", "USD_JPY"]})
    now = candles.index[-1] + pd.Timedelta(hours=2)
    for pair in ("EUR_USD", "XAU_USD"):  # modelos ya guardados (pequeños, datos sintéticos)
        serving.load_or_train(instrument_config(cfg, pair), tmp_path, log=lambda m: None, fetch=fake_fetch, now=now)

    strategies = engine.build_ml_strategies(cfg, tmp_path)
    # USD_JPY está activo pero no llega por FOREX_PAIRS: se omite (y se avisa en el log)
    assert [s.pair for s in strategies] == ["EUR_USD", "XAU_USD"]
    gold = strategies[1]
    assert gold.spread_pips == 50.0 and gold.supports("XAU_USD", "H1") and not gold.supports("EUR_USD", "H1")
