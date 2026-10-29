"""
Utilidades de divisas compartidas: tamaño de pip, conversión de PnL a la
moneda de la cuenta (USD) y especificación de símbolos para simulación.

Única fuente de esta lógica para risk-manager, execution-engine y backtesting.
"""
from typing import Optional

from shared.models import SymbolSpec

STANDARD_LOT = 100_000
GOLD_LOT = 100  # 1 lote de oro = 100 onzas

# Costes de un broker minorista por instrumento (pips de get_pip_size): los usa la
# investigación (config.yaml -> instrument_config) y el broker simulado en vivo.
# Fijados el 2026-10-28 ANTES de validar: spread mediano de Dukascopy de los últimos
# 60 días + el recargo de un broker minorista (como EUR_USD: 0.3 -> 1.0).
# Oro: pip 0.01 USD; spread 50 = $0.50 por onza, slippage 10 = $0.10.
INSTRUMENT_COSTS = {
    "EUR_USD": {"spread_pips": 1.0, "slippage_pips": 0.3},
    "GBP_USD": {"spread_pips": 1.3, "slippage_pips": 0.3},
    "USD_JPY": {"spread_pips": 1.2, "slippage_pips": 0.3},
    "AUD_USD": {"spread_pips": 1.3, "slippage_pips": 0.3},
    "XAU_USD": {"spread_pips": 50.0, "slippage_pips": 10.0},
}
DEFAULT_COSTS = {"spread_pips": 1.5, "slippage_pips": 0.3}


def normalize_pair(pair: str) -> str:
    return pair.upper().replace("/", "_").replace("-", "_")


def contract_size(pair: str) -> int:
    """Unidades por lote estándar: 100.000 en divisas, 100 onzas en oro."""
    return GOLD_LOT if normalize_pair(pair).startswith("XAU") else STANDARD_LOT


def instrument_costs(pair: str) -> dict:
    return dict(INSTRUMENT_COSTS.get(normalize_pair(pair), DEFAULT_COSTS))


def max_spread_pips(pair: str, configured: float) -> float:
    """
    Spread máximo aceptable: el configurado (MAX_SPREAD_PIPS, pensado para
    divisas) o 2.5 veces el típico del instrumento si es mayor (oro).
    """
    return max(configured, 2.5 * instrument_costs(pair)["spread_pips"])


def get_pip_size(pair: str) -> float:
    """Tamaño de 1 pip para el par (0.01 en pares JPY y oro, 0.0001 en el resto)."""
    p = normalize_pair(pair)
    if "JPY" in p or "XAU" in p:
        return 0.01
    return 0.0001


def quote_to_usd_rate(pair: str, price: float) -> Optional[float]:
    """
    Factor para convertir montos en la divisa cotizada a USD.
      XXX_USD -> 1 (ya está en USD)
      USD_XXX -> 1 / precio
      Cruces (EUR_GBP, ...) -> None: hace falta otro tipo de cambio; en vivo
      lo resuelve MT5 con tick_value, en simulación se rechaza el par.
    """
    p = normalize_pair(pair)
    if p.endswith("_USD"):
        return 1.0
    if p.startswith("USD_") and price > 0:
        return 1.0 / price
    return None


def financing_cost_usd(pair: str, units: float, price: float, days: float, annual_pct: float) -> float:
    """
    Swap (financiamiento nocturno) aproximado: `annual_pct` % anual sobre el
    nocional, cobrado por cada día de calendario que la posición sigue
    abierta (los fines de semana cuentan, como el swap triple del miércoles).
    Siempre es un costo, en ambas direcciones: es la aproximación conservadora.
    """
    rate = quote_to_usd_rate(pair, price)
    if not annual_pct or days <= 0 or rate is None:
        return 0.0
    return units * price * rate * annual_pct / 100.0 * days / 365.0


def pnl_in_usd(pair: str, price_diff: float, units: float, exit_price: float) -> Optional[float]:
    """PnL en USD de una posición de `units` con una diferencia de precio `price_diff`."""
    rate = quote_to_usd_rate(pair, exit_price)
    if rate is None:
        return None
    return price_diff * units * rate


def simulated_symbol_spec(pair: str, price: float, spread_pips: Optional[float] = None) -> Optional[SymbolSpec]:
    """
    Especificación de símbolo estilo MT5 para el modo simulación, con cuenta en USD.
    Devuelve None para cruces sin conversión directa a USD.
    """
    rate = quote_to_usd_rate(pair, price)
    if rate is None:
        return None
    if spread_pips is None:
        spread_pips = instrument_costs(pair)["spread_pips"]
    pip = get_pip_size(pair)
    point = pip / 10.0
    lot = contract_size(pair)
    return SymbolSpec(
        pair=normalize_pair(pair),
        symbol=normalize_pair(pair).replace("_", ""),
        digits=3 if pip == 0.01 else 5,
        point=point,
        tick_size=point,
        tick_value=point * lot * rate,
        contract_size=lot,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0,
        spread_points=int(round(spread_pips * 10)),
        currency_profit=normalize_pair(pair).split("_")[-1],
    )
