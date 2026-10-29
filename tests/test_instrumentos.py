"""
Varios instrumentos: el oro se dimensiona por onzas (lote de 100), con su
propio spread, y la regla pre-registrada decide qué se activa.
"""
import pytest

from position_sizing import calculate_lots
from run_instrumentos import paper_eligible, real_money_eligible
from shared.config import instrument_config
from shared.fx import contract_size, instrument_costs, max_spread_pips, simulated_symbol_spec

CFG = {"mercado": {"par": "EUR_USD", "temporalidad": "H1"},
       "costes": {"spread_pips": 1.0, "slippage_pips": 0.3, "comision_por_lote": 7.0}}


def test_gold_lot_is_100_ounces():
    assert contract_size("XAU_USD") == 100 and contract_size("EUR_USD") == 100_000


def test_gold_position_risks_one_percent():
    # $10.000 al 1 % = $100; SL a $20 por onza -> 5 onzas = 0.05 lotes
    spec = simulated_symbol_spec("XAU_USD", 4000.0)
    lots = calculate_lots(10_000, 1.0, 4000.0, 3980.0, spec)
    assert lots == pytest.approx(0.05)
    assert lots * spec.contract_size * 20 == pytest.approx(100)


def test_forex_sizing_unchanged():
    spec = simulated_symbol_spec("EUR_USD", 1.1)
    assert calculate_lots(10_000, 1.0, 1.1000, 1.0980, spec) == pytest.approx(0.5)  # 20 pips


def test_spread_limit_allows_gold_but_not_wide_forex():
    assert max_spread_pips("EUR_USD", 3.0) == 3.0
    assert max_spread_pips("XAU_USD", 3.0) == pytest.approx(125.0)
    assert instrument_costs("XAU_USD")["spread_pips"] < max_spread_pips("XAU_USD", 3.0)


def test_instrument_config_overrides_market_and_costs_only():
    icfg = instrument_config(CFG, "xau_usd", "m15")
    assert icfg["mercado"] == {"par": "XAU_USD", "temporalidad": "M15"}
    assert icfg["costes"] == {"spread_pips": 50.0, "slippage_pips": 10.0, "comision_por_lote": 7.0}
    assert CFG["mercado"]["par"] == "EUR_USD"  # no modifica el original


def test_activation_rules():
    row = {"veredicto": "POSITIVO PERO NO CONCLUYENTE", "retorno_%": 2.4, "p_valor": 0.31}
    assert paper_eligible(row) and not real_money_eligible(row, 15)
    assert not paper_eligible(dict(row, **{"retorno_%": -1.0}))
    assert not paper_eligible(dict(row, veredicto="SIN VENTAJA"))
    strong = {"veredicto": "VENTAJA CON EVIDENCIA", "retorno_%": 10, "p_valor": 0.01}
    assert not real_money_eligible(strong, 15)          # 0.01 > 0.05 / 15
    assert real_money_eligible(dict(strong, p_valor=0.001), 15)


# ------------------------------------------------------------- swing: swap y Bonferroni acumulado

def test_swap_is_annual_rate_on_notional_per_calendar_day():
    from shared.fx import financing_cost_usd

    # 100.000 EUR a 1.10 = 110.000 USD de nocional; 3 % anual durante 1 año
    assert financing_cost_usd("EUR_USD", 100_000, 1.10, 365, 3.0) == pytest.approx(3300)
    # USD_JPY: el nocional ya está en USD (100.000 USD), 10 días
    assert financing_cost_usd("USD_JPY", 100_000, 157.0, 10, 3.0) == pytest.approx(100_000 * 0.03 * 10 / 365)
    # oro: 5 onzas a 4000 = 20.000 USD, 30 días
    assert financing_cost_usd("XAU_USD", 5, 4000.0, 30, 3.0) == pytest.approx(20_000 * 0.03 * 30 / 365)
    assert financing_cost_usd("EUR_USD", 100_000, 1.10, 10, 0.0) == 0.0


def test_engine_charges_swap_only_when_configured():
    import pandas as pd
    from engine import BacktestEngine
    from shared.models import OrderSide

    trade = {"side": OrderSide.BUY, "entry_price": 1.10, "units": 100_000,
             "entry_time": pd.Timestamp("2026-01-05", tz="UTC")}
    exit_time = pd.Timestamp("2026-01-15", tz="UTC")
    no_swap = BacktestEngine().trade_pnl(trade, 1.11, "EUR_USD", exit_time)[0]
    with_swap = BacktestEngine(financing_annual_pct=3.0).trade_pnl(trade, 1.11, "EUR_USD", exit_time)[0]
    assert no_swap == pytest.approx(1000)
    assert no_swap - with_swap == pytest.approx(110_000 * 0.03 * 10 / 365)


def test_bonferroni_counts_all_previous_tests():
    from run_instrumentos import summarize

    cfg = {"validacion": {"inicio_prueba_final": "2023-10-27"}}
    row = {"instrumento": "XAU_USD", "temporalidad": "H4", "retorno_%": 9.0, "buy_hold_%": 50.0, "max_dd_%": 5.0,
           "profit_factor": 1.3, "operaciones": 60, "win_rate_%": 45.0, "expectativa_r": 0.2, "ic_bajo": 0.05,
           "ic_alto": 0.4, "p_valor": 0.004, "veredicto": "VENTAJA CON EVIDENCIA", "informe": "x"}
    md = summarize([row], cfg, previous_tests=15, title="swing")
    assert "16 combinaciones probadas en total" in md and "p < 0.0031" in md
    assert real_money_eligible(row, 10) and not real_money_eligible(row, 16)
