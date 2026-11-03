"""
Calibración del modelo: qué pasó de verdad en cada tramo de probabilidad,
siempre con el modelo de cada ventana entrenado solo con el pasado.
"""
import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from indicators import calculate_atr
from ml.labels import triple_barrier_labels, triple_barrier_outcomes
from run_calibracion import band_label, calibration_table, oos_predictions

CFG = {
    "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
    "riesgo": {"atr_periodo": 14, "sl_atr": 2.0, "tp_atr": 3.0},
    "modelo": {"horizonte_velas": 24, "umbral_probabilidad": 0.5, "semilla": 1,
               "lightgbm": {"n_estimators": 30, "num_leaves": 7, "min_child_samples": 50}},
    "validacion": {"entrenamiento_inicial_anios": 0.25, "ventana_prueba_meses": 1, "inicio_prueba_final": "2020-01-01"},
}


@pytest.fixture(scope="module")
def candles():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=4000, granularity_minutes=60, seed=9)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df


def test_outcomes_agree_with_labels(candles):
    """«tp» es exactamente la etiqueta 1 del modelo; «sl» y «ninguno» son lo que la etiqueta junta en 0."""
    atr = calculate_atr(candles["high"], candles["low"], candles["close"], 14)
    labels = triple_barrier_labels(candles, atr, 24, 2.0, 3.0, "EUR_USD", 1.0, 0.3)
    outcomes = triple_barrier_outcomes(candles, atr, 24, 2.0, 3.0, "EUR_USD", 1.0, 0.3)
    valid = labels["long"].notna()
    assert (outcomes["long"].isna() == ~valid).all()
    for side in ("long", "short"):
        pd.testing.assert_series_equal((outcomes.loc[valid, side] == "tp").astype(float), labels.loc[valid, side],
                                       check_names=False)
    assert set(outcomes.loc[valid, "long"]) <= {"tp", "sl", "ninguno"}


def test_predictions_only_cover_the_test_period_and_never_see_the_future(candles):
    cfg = dict(CFG, validacion=dict(CFG["validacion"], inicio_prueba_final=str(candles.index[2500].date())))
    data = oos_predictions(candles, cfg, "EUR_USD", log=lambda m: None)
    assert data.index[0] >= pd.Timestamp(cfg["validacion"]["inicio_prueba_final"], tz="UTC")
    p = data[["p_long", "p_short"]]
    assert ((p >= 0) & (p <= 1)).all().all()

    # Cambiar el futuro no cambia lo que dijo el modelo en el pasado
    cut = data.index[len(data) // 2]
    altered = candles.copy()
    altered.loc[altered.index > cut, ["open", "high", "low", "close"]] *= 1.3
    again = oos_predictions(altered, cfg, "EUR_USD", log=lambda m: None)
    before = data.index[data.index < cut - pd.Timedelta(days=40)]
    pd.testing.assert_frame_equal(data.loc[before, ["p_long", "p_short"]], again.loc[before, ["p_long", "p_short"]])


def test_calibration_table():
    data = pd.DataFrame({"p_long": [0.42, 0.44, 0.43, 0.43, 0.60], "res_long": ["tp", "sl", "sl", "ninguno", "tp"],
                         "p_short": [0.2] * 5, "res_short": ["sl"] * 5})
    t = calibration_table(data, rr=1.5)
    row = t[(t["operación"] == "Compra") & (t["probabilidad del modelo"] == "40–45 %")].iloc[0]
    assert row["horas"] == 4 and row["tocó objetivo"] == "25 %" and row["tocó stop"] == "50 %"
    assert row["ninguno en 48 velas"] == "25 %" and row["R aprox."] == "-0.12"  # 0.25 × 1.5 − 0.5
    assert t[t["operación"] == "Venta"]["probabilidad del modelo"].tolist() == ["menos de 30 %"]
    assert band_label(0.55, 1.0001) == "55 % o más"
