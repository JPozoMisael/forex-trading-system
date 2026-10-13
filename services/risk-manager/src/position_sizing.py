"""
Módulo de dimensionamiento de posición (Position Sizing).

Calcula cuánto operar para no arriesgar más de `risk_pct`% del balance si se
toca el Stop Loss. Regla de oro: siempre se redondea HACIA ABAJO; si el tamaño
mínimo del broker ya supera el riesgo permitido, no se opera.
"""
import math
from typing import Optional

from shared.fx import get_pip_size, quote_to_usd_rate  # re-export para compatibilidad
from shared.logger import get_logger
from shared.models import SymbolSpec

log = get_logger(__name__)

__all__ = ["get_pip_size", "calculate_lots", "calculate_position_size"]


def calculate_lots(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    spec: SymbolSpec,
) -> float:
    """
    Lotes a operar usando la especificación del broker (MT5 symbol_info).

    tick_value es lo que vale 1 tick para 1 lote en la divisa de la cuenta, y MT5
    ya incluye la conversión para cruces (EUR_GBP, AUD_JPY...), así que esta
    fórmula sirve para cualquier par:

        riesgo_por_lote = (distancia_SL / tick_size) * tick_value
        lotes = riesgo_permitido / riesgo_por_lote   (redondeado hacia abajo al volume_step)
    """
    if account_balance <= 0 or risk_pct <= 0:
        return 0.0
    if spec.tick_size <= 0 or spec.tick_value <= 0 or spec.volume_step <= 0:
        log.warning(f"Especificación inválida para {spec.pair}: {spec}")
        return 0.0

    sl_distance = abs(entry_price - stop_loss_price)
    if sl_distance < get_pip_size(spec.pair):
        log.warning(f"Stop Loss demasiado ajustado (< 1 pip) en {spec.pair}.")
        return 0.0

    risk_amount = account_balance * (risk_pct / 100.0)
    risk_per_lot = (sl_distance / spec.tick_size) * spec.tick_value
    raw_lots = risk_amount / risk_per_lot

    # floor al paso de volumen (con tolerancia para errores de coma flotante)
    lots = math.floor(raw_lots / spec.volume_step + 1e-9) * spec.volume_step
    lots = min(round(lots, 8), spec.volume_max)

    if lots < spec.volume_min:
        log.info(
            f"{spec.pair}: el lote mínimo ({spec.volume_min}) arriesgaría más del {risk_pct}% "
            f"(calculado {raw_lots:.4f} lotes). No se opera."
        )
        return 0.0
    return lots


def calculate_position_size(
    account_balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss_price: float,
    pair: str,
    max_leverage: float = 30.0,
    min_units: int = 1,
    quote_to_account_rate: Optional[float] = None,
) -> int:
    """
    Versión en UNIDADES para cuentas en USD (usada por el backtester).

        Units = Riesgo_USD / (Distancia_SL * conversión_divisa_cotizada->USD)

    Para cruces sin USD hay que pasar `quote_to_account_rate`; si no, retorna 0
    en lugar de inventar un tamaño.
    """
    if account_balance <= 0:
        log.warning("Balance de cuenta inválido (<= 0).")
        return 0
    if entry_price <= 0 or stop_loss_price <= 0:
        log.warning("Precio de entrada o Stop Loss inválido (<= 0).")
        return 0

    price_diff = abs(entry_price - stop_loss_price)
    if price_diff / get_pip_size(pair) < 1.0:
        log.warning("Stop Loss demasiado ajustado (< 1 pip).")
        return 0

    rate = quote_to_account_rate if quote_to_account_rate is not None else quote_to_usd_rate(pair, entry_price)
    if rate is None:
        log.warning(f"{pair}: falta el tipo de cambio de la divisa cotizada a USD; no se dimensiona.")
        return 0

    risk_amount_usd = account_balance * (risk_pct / 100.0)
    units = int(risk_amount_usd / (price_diff * rate))

    # Límite de apalancamiento: valor nocional en USD <= balance * apalancamiento
    base_to_usd = entry_price * rate  # precio de 1 unidad de la divisa base en USD
    max_allowed_units = int((account_balance * max_leverage) / base_to_usd) if base_to_usd > 0 else units
    final_units = min(units, max_allowed_units)

    if final_units < min_units:
        return 0
    return final_units
