"""
Simulador del seguimiento de tendencia PRE-REGISTRADO en config.yaml (sección
tendencia, 2026-11-07): ruptura de Donchian 55/20 con stop a 2 ATR, en velas
diarias. Las reglas están escritas ahí; esto las traduce.

La salida depende del canal de 20 velas (no hay objetivo fijo), algo que el
motor general no tiene. Convenciones de ejecución, las mismas del motor:
  - Velas de precio MEDIO: se compra al ask (medio + medio spread) y se vende
    al bid; slippage en la entrada y en todas las salidas.
  - Señal con la vela cerrada; entrada y salida por canal a la apertura
    siguiente. El stop se mira en todas las velas, incluida la de entrada; si
    la vela abre más allá del stop (hueco), se sale en la apertura.
  - Comisión por lote y swap diario sobre el nocional, en R.
Cada operación lleva también el R de la OPUESTA (mismo momento y distancia de
stop, dirección contraria, sale cuando sale la original o en su propio stop),
para el test de permutación.
"""
from dataclasses import dataclass
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from indicators import calculate_atr
from shared.fx import contract_size, get_pip_size, instrument_costs, quote_to_usd_rate


@dataclass
class Params:
    entrada_velas: int = 55
    salida_velas: int = 20
    stop_atr: float = 2.0
    atr_periodo: int = 20
    financiamiento_anual_pct: float = 3.0
    comision_por_lote: float = 7.0

    @classmethod
    def from_config(cls, sec: Dict[str, Any], commission: float) -> "Params":
        kw = {k: sec[k] for k in cls.__dataclass_fields__ if k in sec}
        return cls(**kw, comision_por_lote=commission)


def channels(df: pd.DataFrame, p: Params) -> pd.DataFrame:
    """Canales de las velas ANTERIORES (no incluyen la actual) y ATR de cada vela."""
    h, l, c = df["high"], df["low"], df["close"]
    return pd.DataFrame({
        "hi_in": h.rolling(p.entrada_velas).max().shift(1), "lo_in": l.rolling(p.entrada_velas).min().shift(1),
        "hi_out": h.rolling(p.salida_velas).max().shift(1), "lo_out": l.rolling(p.salida_velas).min().shift(1),
        "atr": calculate_atr(h, l, c, p.atr_periodo)}, index=df.index)


def _costs_r(pair: str, entry: float, stop_dist: float, days: float, p: Params) -> float:
    """Comisión + swap de la operación, en R (por unidad: el tamaño se cancela)."""
    rate = quote_to_usd_rate(pair, entry) or 1.0
    risk_usd = stop_dist * rate
    comm = p.comision_por_lote / contract_size(pair)
    swap = entry * rate * p.financiamiento_anual_pct / 100 * max(days, 0.0) / 365
    return (comm + swap) / risk_usd


def _gross_r(pair: str, d: int, entry: float, exit_px: float, stop_dist: float) -> float:
    rate_in, rate_out = quote_to_usd_rate(pair, entry) or 1.0, quote_to_usd_rate(pair, exit_px) or 1.0
    return d * (exit_px - entry) * rate_out / (stop_dist * rate_in)


def _opposite_r(pair, d, i0, x_i, x_reason, o, h, l, c, hs, slip, stop_dist, idx, p) -> float:
    """La operación contraria desde la misma vela: sale en su stop o cuando salió la original."""
    od = -d
    entry = o[i0] + od * (hs + slip)
    stop = entry - od * stop_dist
    exit_px, exit_i = None, x_i
    for i in range(i0, x_i + 1):
        worst = l[i] - hs if od > 0 else h[i] + hs
        if od * (worst - stop) <= 0:
            open_px = o[i] - od * hs
            exit_px = (open_px if (i > i0 and od * (open_px - stop) <= 0) else stop) - od * slip
            exit_i = i
            break
    if exit_px is None:
        ref = o[x_i] if x_reason == "canal" else c[x_i]
        exit_px = ref - od * hs - od * slip
    days = (idx[exit_i] - idx[i0]).total_seconds() / 86400
    return _gross_r(pair, od, entry, exit_px, stop_dist) - _costs_r(pair, entry, stop_dist, days, p)


def simulate(df: pd.DataFrame, pair: str, p: Params) -> pd.DataFrame:
    costs, pip = instrument_costs(pair), get_pip_size(pair)
    hs, slip = costs["spread_pips"] * pip / 2, costs["slippage_pips"] * pip
    ch = channels(df, p)
    o, h, l, c = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low", "close"))
    hi_in, lo_in, hi_out, lo_out, atr = (ch[k].to_numpy() for k in ("hi_in", "lo_in", "hi_out", "lo_out", "atr"))
    idx, n = df.index, len(df)
    trades: List[Dict[str, Any]] = []
    pos = None            # posición abierta
    pending_entry = None  # dirección a abrir en la apertura de esta vela
    exit_next = False     # salida por canal en la apertura de esta vela

    def close(i, exit_px, reason):
        nonlocal pos
        days = (idx[i] - idx[pos["i"]]).total_seconds() / 86400
        r = _gross_r(pair, pos["d"], pos["entry"], exit_px, pos["dist"]) - _costs_r(pair, pos["entry"], pos["dist"],
                                                                                     days, p)
        r_opp = _opposite_r(pair, pos["d"], pos["i"], i, reason, o, h, l, c, hs, slip, pos["dist"], idx, p)
        trades.append({"pair": pair, "side": "buy" if pos["d"] > 0 else "sell", "entry_time": idx[pos["i"]],
                       "exit_time": idx[i], "entry_price": pos["entry"], "stop_loss": pos["stop"],
                       "exit_price": exit_px, "exit_reason": reason, "dias": days, "r": r, "r_opposite": r_opp})
        pos = None

    for i in range(n):
        # 1. Órdenes de la vela anterior, a la apertura de esta
        if exit_next and pos is not None:
            close(i, o[i] - pos["d"] * (hs + slip), "canal")
        exit_next = False
        if pending_entry is not None and pos is None:
            d = pending_entry
            entry = o[i] + d * (hs + slip)
            dist = p.stop_atr * atr[i - 1]
            if dist > 0:
                pos = {"d": d, "i": i, "entry": entry, "dist": dist, "stop": entry - d * dist}
        pending_entry = None
        # 2. Stop dentro de la vela (incluida la de entrada)
        if pos is not None:
            d = pos["d"]
            worst = l[i] - hs if d > 0 else h[i] + hs
            if d * (worst - pos["stop"]) <= 0:
                open_px = o[i] - d * hs
                gap = i > pos["i"] and d * (open_px - pos["stop"]) <= 0
                close(i, (open_px if gap else pos["stop"]) - d * slip, "stop")
        if i == n - 1:
            break
        # 3. Señales con la vela cerrada (se ejecutan en la siguiente)
        if pos is not None:
            d = pos["d"]
            if (d > 0 and c[i] < lo_out[i]) or (d < 0 and c[i] > hi_out[i]):
                exit_next = True
        elif not np.isnan(hi_in[i]) and not np.isnan(atr[i]):
            if c[i] > hi_in[i]:
                pending_entry = 1
            elif c[i] < lo_in[i]:
                pending_entry = -1
    if pos is not None:
        close(n - 1, c[n - 1] - pos["d"] * (hs + slip), "fin_datos")
    return pd.DataFrame(trades)
