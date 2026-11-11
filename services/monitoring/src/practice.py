"""
Operación de práctica de cada reporte: entre los instrumentos que opera el
modelo, la compra o venta de mayor probabilidad aunque no llegue al umbral.

Sirve para acumular operaciones y medir en vivo qué pasa con las "mejores" del
modelo. Va por el risk-manager y el execution-engine como cualquier señal (con
los mismos SL/TP en ATR) y manda los mismos correos, pero con la estrategia
"practica": se cuenta aparte, no mueve el balance simulado y no bloquea las
señales del modelo. Solo con DRY_RUN.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, List, Optional

from shared.config import settings
from shared.logger import get_logger
from shared.models import PRACTICE_STRATEGY, Signal, SignalDirection
from shared.timefmt import to_local

log = get_logger(__name__)


@dataclass
class Practice:
    pair: str
    buy: bool
    prob: float
    umbral: float
    entry: float
    stop_loss: float
    take_profit: float


def practice_enabled() -> bool:
    return settings.enable_practice_trades and settings.dry_run


def pick_practice(datas: List[Any], now: datetime) -> Optional[Practice]:
    """
    La operación de mayor probabilidad entre los instrumentos con el análisis al
    día y sin posición abierta. None si ninguno sirve.
    """
    from daily_report import _model_plan, _plan_levels

    max_age = timedelta(minutes=2 * settings.granularity_minutes + 10)
    best: Optional[Practice] = None
    for d in datas:
        plan = _model_plan(d.strategy_state)
        if plan is None or d.candles.empty or not d.open_orders.empty:
            continue
        evaluated = (d.strategy_state or {}).get("evaluada")
        if evaluated is None or now - to_local(evaluated) > max_age:
            continue  # análisis viejo (strategy-engine detenido): no se opera con él
        entry = float(d.candles["close"].iloc[-1])
        for buy in (True, False):
            prob = float(plan["p_long" if buy else "p_short"])
            if best is None or prob > best.prob:
                sl, tp = _plan_levels(plan, entry, buy)
                best = Practice(d.pair, buy, prob, float(plan["umbral"]), entry, round(sl, 5), round(tp, 5))
    return best


def practice_signal(pr: Practice, now: datetime) -> Signal:
    return Signal(
        pair=pr.pair,
        strategy_name=PRACTICE_STRATEGY,
        direction=SignalDirection.BUY if pr.buy else SignalDirection.SELL,
        confidence=round(pr.prob, 4),
        timestamp=now,
        entry_price=pr.entry,
        stop_loss=pr.stop_loss,
        take_profit=pr.take_profit,
        metadata={"practica": True, "umbral": pr.umbral},
    )


def publish_practice(bus, pr: Practice, now: datetime) -> bool:
    try:
        bus.publish(settings.channel_signals_new, practice_signal(pr, now))
        log.info(f"🎯 Operación de práctica enviada: {pr.pair} {'BUY' if pr.buy else 'SELL'} ({pr.prob * 100:.0f} %)")
        return True
    except Exception as e:
        log.error(f"No se pudo enviar la operación de práctica: {e}")
        return False
