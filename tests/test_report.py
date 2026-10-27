"""
Validación + informe de punta a punta con datos sintéticos pequeños: se
generan el gráfico, el markdown con la respuesta sobre las líneas base y el
CSV de operaciones.
"""
from data_loader import generate_synthetic_forex_data
from report import write_report
from strategies.ma_crossover import MACrossoverStrategy
from validation import BUY_AND_HOLD, NO_TRADE, validate

CFG = {
    "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
    "riesgo": {"capital_inicial": 10000, "riesgo_por_operacion_pct": 1.0, "perdida_diaria_max_pct": 5.0},
    "validacion": {"entrenamiento_inicial_anios": 0.1, "ventana_prueba_meses": 1, "n_permutaciones": 200,
                   "min_operaciones": 5, "expectativa_minima_r": 0.05},
}


def test_validation_and_report_end_to_end(tmp_path):
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=24 * 120, granularity_minutes=60, seed=9)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    factories = {"regla": lambda train, w: MACrossoverStrategy(fast_period=5, slow_period=15, use_trend_filter=False)}

    report = validate(df, factories, "EUR_USD", "H1", CFG, log=lambda m: None)
    assert set(report.results) == {NO_TRADE, BUY_AND_HOLD, "regla"}
    assert len(report.windows) >= 2
    assert report.results["regla"].metrics.total_trades > 0
    assert "regla" in report.verdicts

    out = write_report(report, {"regla": "Regla"}, main_strategy="regla", out_dir=tmp_path / "informe")
    md = (out / "informe.md").read_text(encoding="utf-8")
    assert (out / "equity.png").stat().st_size > 10_000
    assert (out / "operaciones_regla.csv").exists()
    assert "¿El modelo supera a las líneas base?" in md
    assert "| Regla |" in md
