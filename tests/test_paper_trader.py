"""
El paper trading debe dar las MISMAS operaciones que el backtester sobre las
mismas velas (mismas reglas de ejecución), también si se reinicia a mitad de
camino (estado guardado en disco).
"""
import csv

import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from engine import BacktestEngine
from paper_trader import PaperTrader
from strategies.ma_crossover import MACrossoverStrategy

CFG = {
    "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0},
    "riesgo": {"capital_inicial": 10000, "riesgo_por_operacion_pct": 1.0, "perdida_diaria_max_pct": 2.0},
}


@pytest.fixture(scope="module")
def candles():
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=1500, granularity_minutes=60, seed=5)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index
    return df


def strategy():
    return MACrossoverStrategy(fast_period=5, slow_period=15, use_trend_filter=False)


def backtest_trades(df):
    engine = BacktestEngine(
        initial_balance=10000, risk_per_trade_pct=1.0, spread_pips=1.0, slippage_pips=0.3,
        commission_per_lot=7.0, max_daily_loss_pct=2.0,
    )
    res = engine.run(strategy(), df, pair="EUR_USD")
    return [t for t in res.trades if t["exit_reason"] != "end_of_data"]


def paper_trades(log_dir):
    with open(log_dir / "operaciones.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_paper_trading_matches_backtest(candles, tmp_path):
    expected = backtest_trades(candles)
    assert len(expected) > 10

    s = strategy()
    trader = PaperTrader(s, "EUR_USD", CFG, tmp_path, log=lambda m: None)
    trader.start(candles.iloc[: s.min_candles_required])  # el backtester empieza a operar en esta vela
    trader.process(candles)

    got = paper_trades(tmp_path)
    assert len(got) == len(expected)
    for g, e in zip(got, expected):
        assert pd.Timestamp(g["entrada"]) == e["entry_time"]
        assert pd.Timestamp(g["salida"]) == e["exit_time"]
        assert g["lado"] == e["side"]
        assert g["motivo"] == e["exit_reason"]
        assert float(g["pnl"]) == pytest.approx(e["pnl"], abs=0.01)


def test_paper_trading_survives_restart(candles, tmp_path):
    expected = backtest_trades(candles)
    s = strategy()
    first = PaperTrader(s, "EUR_USD", CFG, tmp_path, log=lambda m: None)
    first.start(candles.iloc[: s.min_candles_required])
    first.process(candles.iloc[:700])

    # Nuevo proceso: lee estado.json y sigue desde la última vela procesada
    second = PaperTrader(strategy(), "EUR_USD", CFG, tmp_path, log=lambda m: None)
    assert second.state["ultima_vela"] == str(candles.index[699])
    second.process(candles)

    got = paper_trades(tmp_path)
    assert [pd.Timestamp(g["entrada"]) for g in got] == [e["entry_time"] for e in expected]
    assert float(got[-1]["balance"]) == pytest.approx(expected[-1]["balance_after"], abs=0.05)


def test_first_run_does_not_trade_the_past(candles, tmp_path):
    trader = PaperTrader(strategy(), "EUR_USD", CFG, tmp_path, log=lambda m: None)
    assert trader.process(candles) == 0  # solo marca el punto de partida
    assert not (tmp_path / "operaciones.csv").exists()
    assert trader.state["ultima_vela"] == str(candles.index[-1])
