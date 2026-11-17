"""
Desafío de cuenta pequeña (p. ej. $20 -> $300): cómo iría una cuenta chica que
copiara las operaciones cerradas del sistema (modelo y práctica), arriesgando
un % fijo de su saldo en cada una.

No abre órdenes: recalcula cada operación con su resultado en R (pnl / riesgo)
sobre el saldo del desafío, con interés compuesto, y el lote que habría hecho
falta. Sirve para ver si "la cuenta pequeña" crece o se quema con lo que el
sistema propone, y si el lote es posible en una cuenta normal.
"""
from typing import Any, Dict, List

import pandas as pd

from shared.config import settings

MIN_STANDARD_LOT = 0.01  # lote mínimo de una cuenta estándar


def milestones(start: float, target: float) -> List[float]:
    """Hitos duplicando el saldo hasta la meta: 20 -> 40, 80, 160, 300."""
    out, v = [], start * 2
    while v < target:
        out.append(v)
        v *= 2
    return out + [target]


def challenge_progress(trades: pd.DataFrame, start: float, target: float, risk_pct: float) -> Dict[str, Any]:
    """
    `trades`: operaciones cerradas con pnl, risk_amount, volume_lots, closed_at
    (y opcionales pair, opened_at, kind). Devuelve el saldo, las filas por
    operación y si la cuenta se quemó (saldo < 1 % del inicial).
    """
    balance, rows = start, []
    if not trades.empty:
        trades = trades.sort_values("closed_at")
    for _, t in trades.iterrows():
        risk_orig = t.get("risk_amount")
        if pd.isna(t.get("pnl")) or pd.isna(risk_orig) or not risk_orig:
            continue
        r = float(t["pnl"]) / float(risk_orig)
        risk = balance * risk_pct / 100
        lots = float(t["volume_lots"]) * risk / float(risk_orig) if pd.notna(t.get("volume_lots")) else None
        balance = max(0.0, balance + risk * r)
        rows.append({"trade": t, "r": r, "riesgo": risk, "lote": lots, "saldo": balance})
        if balance < start * 0.01:
            break
    return {"saldo": balance, "filas": rows, "quemada": balance < start * 0.01,
            "progreso_pct": (balance - start) / (target - start) * 100 if target > start else 0.0,
            "hitos": milestones(start, target)}


def challenge_trades(datas) -> pd.DataFrame:
    """Operaciones cerradas del modelo y de la práctica desde CHALLENGE_START, con su tipo."""
    parts = []
    for d in datas:
        for kind, df in (("Modelo", d.closed_orders), ("Práctica", d.practice_closed)):
            if not df.empty:
                parts.append(df.assign(kind=kind, pair=df["pair"] if "pair" in df else d.pair))
    if not parts:
        return pd.DataFrame()
    trades = pd.concat(parts, ignore_index=True)
    if settings.challenge_start:
        since = pd.Timestamp(settings.challenge_start, tz="UTC")
        trades = trades[pd.to_datetime(trades["closed_at"], utc=True) >= since]
    return trades
