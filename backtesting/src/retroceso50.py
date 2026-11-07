"""
Simulador del método "retroceso al 50 %" PRE-REGISTRADO en config.yaml (sección
retroceso_50, 2026-11-06). Las reglas están escritas ahí; esto las traduce.

Usa órdenes LIMITADAS (entrar exactamente en el 50 % del impulso), que el
motor general no tiene: allí toda señal entra a mercado en la apertura
siguiente. Convenciones de ejecución, las mismas del motor:
  - Velas de precio MEDIO: se compra al ask (medio + medio spread) y se vende
    al bid. Una compra limitada se llena cuando el ask toca el precio; si la
    vela abre ya por debajo, se llena en la apertura (mejor precio).
  - El slippage se paga en todas las salidas; si en una vela se tocan stop y
    objetivo, cuenta el stop; en la vela del llenado solo se mira el stop.
  - Comisión por onza ida y vuelta, descontada del resultado.
Resultado de cada operación en R (unidades del riesgo de esa operación) y el
de la operación OPUESTA en el mismo momento (para el test de permutación).
"""
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from indicators import calculate_atr
from ml.tradingview import _rolling_extreme_position
from shared.fx import contract_size, get_pip_size, instrument_costs


@dataclass
class Params:
    ventana_impulso: int = 48
    impulso_min_atr: float = 10.0
    entrada_fib: float = 0.5
    stop_fib: float = 0.618
    stop_margen_atr: float = 0.1
    validez_orden_velas: int = 36
    max_velas_abierta: int = 48

    @classmethod
    def from_config(cls, sec: Dict[str, Any]) -> "Params":
        return cls(**{k: sec[k] for k in cls.__dataclass_fields__ if k in sec})


def impulses(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """
    Para cada vela cerrada: dirección del último impulso de la ventana (+1 subida,
    -1 bajada, 0 no vale), su mínimo y máximo, y los niveles de la orden.
    """
    h, l, c = df["high"], df["low"], df["close"]
    atr = calculate_atr(h, l, c, 14)
    w = p.ventana_impulso
    hi, lo = h.rolling(w).max(), l.rolling(w).min()
    ago_hi = _rolling_extreme_position(h.to_numpy(dtype=float), w, np.argmax)
    ago_lo = _rolling_extreme_position(l.to_numpy(dtype=float), w, np.argmin)
    d = np.where(np.isnan(ago_hi), 0.0, np.where(ago_hi < ago_lo, 1.0, -1.0))  # máximo más reciente = subida
    rng = hi - lo
    # Compra: entrada sobre el mínimo, stop en el 61.8 % de retroceso, objetivo el máximo (espejo para ventas)
    entry = np.where(d > 0, lo + p.entrada_fib * rng, hi - p.entrada_fib * rng)
    stop = np.where(d > 0, hi - p.stop_fib * rng - p.stop_margen_atr * atr,
                    lo + p.stop_fib * rng + p.stop_margen_atr * atr)
    target = np.where(d > 0, hi, lo)
    ok = (rng >= p.impulso_min_atr * atr) & ((c - entry) * d > 0)
    return pd.DataFrame({"dir": np.where(ok, d, 0.0), "entry": entry, "stop": stop, "target": target,
                         "hi": hi, "lo": lo, "atr": atr}, index=df.index)


def _resolve(d: int, fill: float, sl: float, tp: float, start: int, o, h, l, c, hs: float, slip: float,
             max_bars: int):
    """Salida de una posición llenada en la vela `start`: (precio, vela, motivo)."""
    def close_px(mid):  # la compra cierra al bid y la venta al ask
        return mid - d * hs

    n = len(o)
    for i in range(start, min(n, start + max_bars)):
        worst, best = (l[i], h[i]) if d > 0 else (h[i], l[i])
        if d * (close_px(worst) - sl) <= 0:
            gap = d * (close_px(o[i]) - sl) <= 0 and i > start
            return (close_px(o[i]) if gap else sl) - d * slip, i, "sl"
        if i > start and d * (close_px(best) - tp) >= 0:
            return tp - d * slip, i, "tp"
    last = min(n, start + max_bars) - 1
    return close_px(c[last]) - d * slip, last, "tiempo" if start + max_bars <= n else "fin_datos"


def simulate(df: pd.DataFrame, pair: str, p: Params, commission_per_lot: float = 7.0) -> pd.DataFrame:
    """Todas las operaciones del método sobre `df`, una a la vez, con su R y el de la opuesta."""
    costs, pip = instrument_costs(pair), get_pip_size(pair)
    hs, slip = costs["spread_pips"] * pip / 2, costs["slippage_pips"] * pip
    comm = commission_per_lot / contract_size(pair)  # por onza (o unidad), ida y vuelta
    imp = impulses(df, p)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    direction, entry, stop, target = (imp[k].to_numpy() for k in ("dir", "entry", "stop", "target"))
    n, idx = len(df), df.index
    trades: List[Dict[str, Any]] = []
    t = 0
    while t < n - 1:
        d = int(direction[t])
        if d == 0:
            t += 1
            continue
        # Orden limitada colocada al cierre de t, activa desde t+1
        E, SL, TP, extreme = entry[t], stop[t], target[t], target[t]
        filled = None
        last = min(n - 1, t + p.validez_orden_velas)
        for i in range(t + 1, last + 1):
            if d * ((h[i] if d > 0 else l[i]) - extreme) > 0:  # nuevo extremo antes de llenarse: se cancela
                break
            touch = (l[i] if d > 0 else h[i]) + d * hs        # ask mínimo (compra) / bid máximo (venta)
            if d * (E - touch) >= 0:
                open_px = o[i] + d * hs
                filled = (i, open_px if d * (open_px - E) < 0 else E)
                break
        if filled is None:
            t = i if i > t else t + 1  # se busca otra orden desde donde se canceló o venció
            continue
        fi, fill = filled
        risk = abs(fill - SL)
        if risk <= 0:
            t = fi + 1
            continue
        exit_px, xi, reason = _resolve(d, fill, SL, TP, fi, o, h, l, c, hs, slip, p.max_velas_abierta)
        r = (d * (exit_px - fill) - comm) / risk
        # La opuesta: en el mismo momento, al precio de mercado contrario, mismas distancias
        opp_fill = fill - d * 2 * hs
        reward = abs(TP - fill)
        opp_exit, _, _ = _resolve(-d, opp_fill, opp_fill + d * risk, opp_fill - d * reward, fi, o, h, l, c, hs,
                                  slip, p.max_velas_abierta)
        r_opp = (-d * (opp_exit - opp_fill) - comm) / risk
        trades.append({"side": "buy" if d > 0 else "sell", "placed_time": idx[t], "entry_time": idx[fi],
                       "exit_time": idx[xi], "entry_price": fill, "stop_loss": SL, "take_profit": TP,
                       "exit_price": exit_px, "exit_reason": reason, "r": r, "r_opposite": r_opp,
                       "impulso_desde": imp["lo"].iloc[t] if d > 0 else imp["hi"].iloc[t],
                       "impulso_hasta": extreme, "riesgo": risk, "beneficio_plan": reward})
        t = xi + 1  # una operación a la vez: la siguiente orden, después de cerrar
    return pd.DataFrame(trades)
