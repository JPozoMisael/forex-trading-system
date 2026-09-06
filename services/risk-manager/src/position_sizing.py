"""
Módulo de dimensionamiento de posición (Position Sizing).
Calcula el número exacto de unidades/lotes según el porcentaje de riesgo por operación,
el balance de la cuenta, la distancia al Stop Loss y el valor del pip por par.
"""
from typing import Optional
from shared.logger import get_logger

log = get_logger(__name__)


def get_pip_size(pair: str) -> float:
    """Retorna el tamaño de 1 pip para el par especificado."""
    pair_clean = pair.upper().replace("/", "_").replace("-", "_")
    if "JPY" in pair_clean or "XAU" in pair_clean:
        return 0.01
    return 0.0001


def calculate_position_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    pair: str,
    max_leverage: float = 30.0,
    min_units: int = 100,
) -> int:
    """
    Calcula el tamaño de posición en unidades para no arriesgar más de `risk_pct`% del balance.

    Fórmula:
        Risk_Amount ($) = Account_Balance * (Risk_Pct / 100)
        Stop_Loss_Distance = abs(Entry_Price - Stop_Loss_Price)
        Units = Risk_Amount / Stop_Loss_Distance (ajustado por valor del pip y divisa)
    """
    if account_balance <= 0:
        log.warning("Balance de cuenta inválido (<= 0).")
        return 0

    if entry_price <= 0 or stop_loss_price <= 0:
        log.warning("Precio de entrada o Stop Loss inválido (<= 0).")
        return 0

    price_diff = abs(entry_price - stop_loss_price)
    if price_diff == 0:
        log.warning("La distancia al Stop Loss es 0.")
        return 0

    pip_size = get_pip_size(pair)
    pips_at_risk = price_diff / pip_size
    if pips_at_risk < 1.0:
        log.warning(f"Stop Loss demasiado ajustado (< 1 pip): {pips_at_risk:.1f} pips.")
        return 0

    risk_amount_usd = account_balance * (risk_pct / 100.0)

    pair_clean = pair.upper().replace("/", "_")
    if pair_clean.endswith("USD"):
        units = risk_amount_usd / (pips_at_risk * pip_size)
    elif pair_clean.startswith("USD"):
        pip_val_per_unit = pip_size / entry_price
        units = risk_amount_usd / (pips_at_risk * pip_val_per_unit)
    else:
        units = risk_amount_usd / price_diff

    max_allowed_units = int((account_balance * max_leverage) / entry_price)
    calculated_units = int(units)

    final_units = min(calculated_units, max_allowed_units)

    if final_units < min_units:
        log.debug(f"Unidades calculadas ({final_units}) menores al mínimo ({min_units}). Asignando {min_units} unidades.")
        return min_units

    log.debug(
        f"Position Sizing para {pair}: Balance=${account_balance:.2f} | Riesgo {risk_pct}% (${risk_amount_usd:.2f}) | "
        f"SL Dist={pips_at_risk:.1f} pips -> Unidades: {final_units}"
    )
    return final_units
