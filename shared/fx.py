"""
Utilidades de divisas compartidas: tamaño de pip, conversión de PnL a la
moneda de la cuenta (USD) y especificación de símbolos para simulación.

Única fuente de esta lógica para risk-manager, execution-engine y backtesting.
"""
from typing import Optional

from shared.models import SymbolSpec

STANDARD_LOT = 100_000


def normalize_pair(pair: str) -> str:
    return pair.upper().replace("/", "_").replace("-", "_")


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


def pnl_in_usd(pair: str, price_diff: float, units: float, exit_price: float) -> Optional[float]:
    """PnL en USD de una posición de `units` con una diferencia de precio `price_diff`."""
    rate = quote_to_usd_rate(pair, exit_price)
    if rate is None:
        return None
    return price_diff * units * rate


def simulated_symbol_spec(pair: str, price: float, spread_pips: float = 1.0) -> Optional[SymbolSpec]:
    """
    Especificación de símbolo estilo MT5 para el modo simulación, con cuenta en USD.
    Devuelve None para cruces sin conversión directa a USD.
    """
    rate = quote_to_usd_rate(pair, price)
    if rate is None:
        return None
    pip = get_pip_size(pair)
    point = pip / 10.0
    return SymbolSpec(
        pair=normalize_pair(pair),
        symbol=normalize_pair(pair).replace("_", ""),
        digits=3 if pip == 0.01 else 5,
        point=point,
        tick_size=point,
        tick_value=point * STANDARD_LOT * rate,
        contract_size=STANDARD_LOT,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level_points=0,
        spread_points=int(round(spread_pips * 10)),
        currency_profit=normalize_pair(pair).split("_")[-1],
    )
