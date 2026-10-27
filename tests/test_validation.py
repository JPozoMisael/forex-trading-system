"""
Pruebas de la validación walk-forward: ventanas, señales solo fuera de
muestra, operación opuesta, permutación, bootstrap, veredicto y buy & hold.
"""
import numpy as np
import pandas as pd
import pytest

from shared.models import Signal, SignalDirection
from strategies.base_strategy import BaseStrategy
from validation import (
    ActiveFrom,
    bootstrap_ci,
    buy_and_hold,
    make_verdict,
    make_windows,
    opposite_r,
    permutation_p_value,
)


def hourly_index(start="2020-01-01", years=4):
    return pd.date_range(start, pd.Timestamp(start) + pd.DateOffset(years=years), freq="h", tz="UTC", inclusive="left")


def test_windows_are_consecutive_and_cover_the_test_period():
    idx = hourly_index(years=4)
    windows = make_windows(idx, initial_train_years=3, test_months=6)
    assert len(windows) == 2
    assert windows[0].test_start == idx[0] + pd.DateOffset(years=3)
    assert all(a.test_end == b.test_start for a, b in zip(windows, windows[1:]))
    assert windows[-1].test_end > idx[-1]
    assert all(w.train_start == idx[0] for w in windows)


def test_short_tail_is_merged_into_last_window():
    # 3 años + 6 meses + 10 días: los 10 días sobrantes no forman una ventana propia
    idx = pd.date_range("2020-01-01", pd.Timestamp("2023-07-11"), freq="h", tz="UTC", inclusive="left")
    windows = make_windows(idx, initial_train_years=3, test_months=6)
    assert len(windows) == 1
    assert windows[0].test_end > idx[-1]


def test_final_windows_start_at_cutoff():
    idx = hourly_index(years=5)
    cutoff = pd.Timestamp("2024-01-01", tz="UTC")
    windows = make_windows(idx, initial_train_years=3, test_months=6, first_test_start=cutoff)
    assert windows[0].test_start == cutoff
    assert windows[0].train_start == idx[0]


def test_development_period_never_sees_final_data():
    from validation import validate

    idx = hourly_index(years=1)
    df = pd.DataFrame({"open": 1.1, "high": 1.1005, "low": 1.0995, "close": 1.1}, index=idx)
    cfg = {
        "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
        "riesgo": {"capital_inicial": 10000, "riesgo_por_operacion_pct": 1.0, "perdida_diaria_max_pct": 5.0},
        "validacion": {"entrenamiento_inicial_anios": 0.25, "ventana_prueba_meses": 2, "n_permutaciones": 50,
                       "min_operaciones": 5, "expectativa_minima_r": 0.05, "inicio_prueba_final": "2020-09-01"},
    }
    seen = []

    def factory(train, w):
        seen.append((train.index[-1], w.test_end))
        return AlwaysBuy()

    report = validate(df, {"s": factory}, "EUR_USD", "H1", cfg, log=lambda m: None, periodo="desarrollo")
    cutoff = pd.Timestamp("2020-09-01", tz="UTC")
    assert all(w.test_end <= cutoff for w in report.windows)
    assert all(last < cutoff for last, _ in seen)
    assert report.results["s"].equity.index.max() < cutoff


def test_selection_rule():
    from run_experiments import select_variant

    rows = [
        {"variante": "a", "operaciones": 500, "expectativa_r": 0.02},
        {"variante": "b", "operaciones": 50, "expectativa_r": 0.30},   # pocas operaciones
        {"variante": "c", "operaciones": 300, "expectativa_r": 0.05},
    ]
    assert select_variant(rows) == "c"
    assert select_variant([{"variante": "a", "operaciones": 500, "expectativa_r": -0.01}]) is None


class AlwaysBuy(BaseStrategy):
    def __init__(self):
        super().__init__(name="AlwaysBuy")

    @property
    def min_candles_required(self):
        return 2

    def evaluate(self, df, pair):
        return Signal(pair=pair, strategy_name=self.name, direction=SignalDirection.BUY, entry_price=1.0)


def test_active_from_blocks_signals_before_test_start():
    idx = hourly_index(years=1)[:10]
    df = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}, index=idx)
    wrapped = ActiveFrom(AlwaysBuy(), start=idx[5])
    assert wrapped.evaluate(df.iloc[:5], "EUR_USD") is None
    assert wrapped.evaluate(df.iloc[:6], "EUR_USD") is not None


def test_opposite_of_winning_buy_is_losing_sell():
    # BUY a 1.1000 con SL/TP a 10 pips; el precio sube y toca el TP.
    idx = hourly_index(years=1)[:4]
    seg = pd.DataFrame(
        {"open": [1.1000, 1.1000, 1.1005, 1.1015], "high": [1.1000, 1.1005, 1.1015, 1.1015],
         "low": [1.1000, 1.0995, 1.1000, 1.1010], "close": [1.1000, 1.1005, 1.1012, 1.1015]},
        index=idx,
    )
    trade = {"entry_time": idx[1], "side": "buy", "entry_price": 1.1000, "stop_loss": 1.0990,
             "take_profit": 1.1010, "units": 100_000}
    # Sin costos, la venta opuesta toca su SL (1.1010) en la vela 2: -1 R
    assert opposite_r(trade, seg, "EUR_USD", 0.0, 0.0, 0.0) == pytest.approx(-1.0)
    # Con costos pierde un poco más
    assert opposite_r(trade, seg, "EUR_USD", 1.0, 0.3, 7.0) < -1.0


def test_permutation_detects_real_edge_and_no_edge():
    r = np.full(100, 1.0)
    assert permutation_p_value(r, -r, n_perm=2000) < 0.01
    rng = np.random.default_rng(0)
    noise = rng.choice([1.0, -1.0], 100)
    assert permutation_p_value(noise, -noise, n_perm=2000) > 0.05


def test_bootstrap_ci_contains_mean():
    r = np.random.default_rng(1).normal(0.2, 1.0, 300)
    lo, hi = bootstrap_ci(r)
    assert lo < r.mean() < hi


def _trades(r, r_opp):
    return pd.DataFrame({"r": r, "r_opposite": r_opp})


def test_verdict_labels():
    kw = dict(n_perm=2000, min_trades=30, min_expectancy_r=0.05)
    assert make_verdict(_trades([0.5] * 10, [-0.6] * 10), **kw).label == "INSUFICIENTE"
    assert make_verdict(_trades([0.5] * 50, [-0.6] * 50), **kw).label == "VENTAJA CON EVIDENCIA"
    assert make_verdict(_trades([-0.2] * 50, [0.1] * 50), **kw).label == "SIN VENTAJA"
    noisy = np.r_[[1.0] * 26, [-1.0] * 24]
    assert make_verdict(_trades(noisy, -noisy - 0.05), **kw).label == "POSITIVO PERO NO CONCLUYENTE"


def test_buy_and_hold_pays_costs_on_flat_market():
    idx = hourly_index(years=1)[:24]
    seg = pd.DataFrame({"open": 1.1, "high": 1.1, "low": 1.1, "close": 1.1}, index=idx)
    equity, trades = buy_and_hold(seg, 10_000.0, "EUR_USD", spread_pips=1.0, slippage_pips=0.3, commission_per_lot=7.0)
    assert equity.iloc[-1] < 10_000.0
    assert trades[0]["pnl"] < 0
