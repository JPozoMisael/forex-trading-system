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
