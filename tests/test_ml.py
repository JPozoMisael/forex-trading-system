"""
Pruebas del modelo predictivo, centradas en que NO haya fuga de datos:
features causales, target que solo mira las N velas siguientes, entrenamiento
que nunca usa resultados posteriores al corte, y la misma probabilidad en el
backtest (caché) que en vivo.
"""
import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from indicators import calculate_atr
from ml.features import FEATURE_COLUMNS, build_features
from ml.labels import triple_barrier_labels
from shared.models import SignalDirection
from strategies.ml_strategy import MLStrategy

CFG = {
    "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
    "riesgo": {"atr_periodo": 14, "sl_atr": 2.0, "tp_atr": 3.0},
    "modelo": {"horizonte_velas": 24, "umbral_probabilidad": 0.5, "semilla": 1,
               "lightgbm": {"n_estimators": 30, "num_leaves": 7, "min_child_samples": 50}},
}


@pytest.fixture(scope="module")
def candles():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=3000, granularity_minutes=60, seed=3)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df


@pytest.mark.parametrize("conjunto", ["base", "extendido"])
def test_features_do_not_change_when_future_changes(candles, conjunto):
    full = build_features(candles, conjunto=conjunto)
    cut = 2000
    altered = candles.copy()
    altered.iloc[cut:, :4] *= 1.5  # futuro completamente distinto
    past_only = build_features(altered, conjunto=conjunto)
    pd.testing.assert_frame_equal(full.iloc[:cut], past_only.iloc[:cut])


@pytest.mark.parametrize("conjunto", ["base", "extendido"])
def test_features_of_a_bar_only_need_history_up_to_it(candles, conjunto):
    full = build_features(candles, conjunto=conjunto)
    truncated = build_features(candles.iloc[:1500], conjunto=conjunto)
    pd.testing.assert_frame_equal(full.iloc[:1500], truncated)


def test_feature_sets_columns():
    assert list(build_features(generate_synthetic_forex_data(n_candles=300)).columns) == FEATURE_COLUMNS
    ext = build_features(generate_synthetic_forex_data(n_candles=300), conjunto="extendido")
    assert list(ext.columns[: len(FEATURE_COLUMNS)]) == FEATURE_COLUMNS
    assert "pos_rango_dia_anterior" in ext.columns
    with pytest.raises(ValueError):
        build_features(generate_synthetic_forex_data(n_candles=300), conjunto="inventado")


def _bars(rows):
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)


def test_label_tp_first_is_one_and_same_bar_is_zero():
    atr = pd.Series(0.0010, index=range(6))
    # Vela 0 = señal; entrada en la apertura de la 1 (1.1000); SL -20 pips, TP +30 pips
    tp_first = _bars([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1010, 1.0995, 1.1005), (1.1005, 1.1035, 1.1000, 1.1030),
                      (1.103, 1.103, 1.103, 1.103), (1.103, 1.103, 1.103, 1.103), (1.103, 1.103, 1.103, 1.103)])
    atr.index = tp_first.index
    lab = triple_barrier_labels(tp_first, atr, horizon=3, sl_atr=2.0, tp_atr=3.0, pair="EUR_USD")
    assert lab["long"].iloc[0] == 1.0
    assert lab["short"].iloc[0] == 0.0

    both = _bars([(1.1, 1.1, 1.1, 1.1), (1.1, 1.1040, 1.0970, 1.1), (1.1, 1.1, 1.1, 1.1),
                  (1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1), (1.1, 1.1, 1.1, 1.1)])
    lab = triple_barrier_labels(both, atr.set_axis(both.index), horizon=3, sl_atr=2.0, tp_atr=3.0, pair="EUR_USD")
    assert lab["long"].iloc[0] == 0.0 and lab["short"].iloc[0] == 0.0


def test_labels_only_look_horizon_bars_ahead(candles):
    atr = calculate_atr(candles["high"], candles["low"], candles["close"], 14)
    h = 24
    base = triple_barrier_labels(candles, atr, h, 2.0, 3.0, "EUR_USD", 1.0, 0.3)
    # Las últimas h velas no tienen futuro completo
    assert base["long"].iloc[-h:].isna().all()
    assert base["long"].iloc[-h - 1 - 10:-h].notna().all()
    # Cambiar una vela solo puede afectar las etiquetas de las h velas anteriores
    t = 1500
    altered = candles.copy()
    altered.iloc[t, 1] += 0.05  # high enorme
    changed = triple_barrier_labels(altered, atr, h, 2.0, 3.0, "EUR_USD", 1.0, 0.3)
    diff = (base.fillna(-1) != changed.fillna(-1)).any(axis=1)
    assert not diff.iloc[: t - h].any()
    assert not diff.iloc[t:].any()


def test_training_never_uses_outcomes_after_the_cut(candles):
    train = candles.iloc[:2500]
    strat = MLStrategy.train(train, CFG, "EUR_USD")
    last_sample = pd.Timestamp(strat.model.meta["hasta"])
    # La última muestra es como mucho la vela T - h: su resultado termina dentro del entrenamiento
    assert last_sample <= train.index[-1 - CFG["modelo"]["horizonte_velas"]]


def test_cached_probabilities_match_live_evaluation(candles):
    strat = MLStrategy.train(candles.iloc[:2500], CFG, "EUR_USD")
    live = MLStrategy.from_config(strat.model, CFG, "EUR_USD")
    cached = MLStrategy.from_config(strat.model, CFG, "EUR_USD").precompute(candles)
    for t in (2600, 2800, 2999):
        hist = candles.iloc[t + 1 - live.lookback_window: t + 1]
        p_live = live.probabilities(hist)
        p_cached = cached.probabilities(hist)
        assert p_live["p_long"] == pytest.approx(p_cached["p_long"], abs=1e-6)
        assert p_live["p_short"] == pytest.approx(p_cached["p_short"], abs=1e-6)


def test_extended_model_remembers_its_features_and_matches_live(candles):
    cfg = {**CFG, "modelo": {**CFG["modelo"], "features": "extendido"}}
    strat = MLStrategy.train(candles.iloc[:2500], cfg, "EUR_USD")
    assert strat.model.conjunto == "extendido"
    assert strat.min_candles_required == 600
    live = MLStrategy.from_config(strat.model, cfg, "EUR_USD")
    cached = MLStrategy.from_config(strat.model, cfg, "EUR_USD").precompute(candles)
    hist = candles.iloc[2999 + 1 - live.lookback_window: 3000]
    assert live.probabilities(hist)["p_long"] == pytest.approx(cached.probabilities(hist)["p_long"], abs=1e-6)


def test_signal_uses_configured_atr_multiples(candles):
    strat = MLStrategy.train(candles.iloc[:2500], CFG, "EUR_USD")
    strat.threshold = 0.0  # fuerza señal para revisar SL/TP
    sig = strat.evaluate(candles.iloc[1500:2600], "EUR_USD")
    assert sig is not None
    sl_d, tp_d = abs(sig.entry_price - sig.stop_loss), abs(sig.take_profit - sig.entry_price)
    # SL/TP van redondeados a 5 decimales: con el ATR pequeño de los datos sintéticos el ratio varía < 1%
    assert tp_d / sl_d == pytest.approx(1.5, rel=1e-2)
    assert sig.direction in (SignalDirection.BUY, SignalDirection.SELL)
