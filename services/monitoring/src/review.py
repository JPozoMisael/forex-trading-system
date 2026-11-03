"""
Repaso para el reporte: qué habría pasado con la compra y la venta que el
modelo planteó en cada vela (opere o no) y si sus probabilidades se cumplen en
vivo. Lee la tabla model_evaluations (la escribe el strategy-engine).

Mismas reglas que el objetivo con que se entrenó el modelo (ml/labels.py):
entrada a la APERTURA de la vela siguiente pagando medio spread y slippage, SL
y TP a sl_atr / tp_atr veces el ATR, SL si ambos caen en la misma vela, y
"ninguno" si en el horizonte no toca ninguno de los dos.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd

from shared.fx import get_pip_size, instrument_costs

RESULT_TEXT = {"tp": "✓ objetivo", "sl": "✗ stop", "en_curso": "⏳ en curso", "ninguno": "— ninguno",
               "sin_entrada": "⏳ sin entrada aún"}
_SIDE_NAMES = {"long": "Compra", "short": "Venta"}
# Tramos para el acumulado en vivo; 50 % es el umbral con que opera
LIVE_BANDS = [(0.0, 0.40, "menos de 40 %"), (0.40, 0.50, "40–50 %"), (0.50, 1.01, "50 % o más (opera)")]


def resolve(ev: Dict[str, Any], candles: pd.DataFrame, pair: str) -> Dict[str, Dict[str, Any]]:
    """
    Resultado de la compra y la venta planteadas en la vela `ev["timestamp"]`
    con las velas que vinieron después: {"long": {"res", "entry", "sl", "tp"}, "short": {...}}.
    """
    out = {side: {"res": "sin_entrada", "entry": None, "sl": None, "tp": None} for side in _SIDE_NAMES}
    atr, horizon = ev.get("atr"), int(ev.get("horizonte_velas") or 48)
    if not atr or pd.isna(atr) or not ev.get("sl_atr") or not ev.get("tp_atr"):
        return out
    # Velas posteriores: búsqueda binaria (no una máscara sobre toda la tabla en cada evaluación)
    i0 = candles.index.searchsorted(pd.Timestamp(ev["timestamp"]), side="right")
    after = candles.iloc[i0:i0 + horizon]
    if after.empty:
        return out
    costs, pip = instrument_costs(pair), get_pip_size(pair)
    hs, slip = costs["spread_pips"] * pip / 2, costs["slippage_pips"] * pip
    o = float(after["open"].iloc[0])
    highs, lows = after["high"].to_numpy(dtype=float), after["low"].to_numpy(dtype=float)
    for side, sign in (("long", 1), ("short", -1)):
        entry = o + sign * (hs + slip)
        sl, tp = entry - sign * ev["sl_atr"] * atr, entry + sign * ev["tp_atr"] * atr
        res = "en_curso" if len(after) < horizon else "ninguno"
        for high, low in zip(highs, lows):
            # La compra cierra al bid (precio medio − medio spread) y la venta al ask
            worst = low - hs if sign > 0 else high + hs
            best = high - hs if sign > 0 else low + hs
            if (worst - sl) * sign <= 0:
                res = "sl"
                break
            if (best - tp) * sign >= 0:
                res = "tp"
                break
        out[side] = {"res": res, "entry": entry, "sl": sl, "tp": tp}
    return out


def resolve_all(evals: pd.DataFrame, candles: pd.DataFrame, pair: str) -> pd.DataFrame:
    """Cada evaluación con el resultado de su compra (res_long) y su venta (res_short)."""
    if evals.empty:
        return evals.assign(res_long=pd.Series(dtype=object), res_short=pd.Series(dtype=object))
    rows = []
    for ev in evals.to_dict("records"):
        r = resolve(ev, candles, pair)
        rows.append(dict(ev, res_long=r["long"]["res"], res_short=r["short"]["res"]))
    return pd.DataFrame(rows)


def last_hours(resolved: pd.DataFrame, now: datetime, hours: int = 24) -> pd.DataFrame:
    if resolved.empty:
        return resolved
    ts = pd.to_datetime(resolved["timestamp"], utc=True)
    return resolved[ts >= pd.Timestamp(now) - timedelta(hours=hours)].sort_values("timestamp")


def summary_text(recent: pd.DataFrame) -> str:
    """Una línea: de N horas, cuántas compras y ventas tocaron objetivo, stop o siguen en curso, y cuántas operó."""
    if recent.empty:
        return "Sin evaluaciones del modelo en las últimas 24 horas."
    parts = []
    for side, name in _SIDE_NAMES.items():
        c = recent[f"res_{side}"].value_counts()
        parts.append(f"{name.lower()}: {c.get('tp', 0)} ✓ objetivo, {c.get('sl', 0)} ✗ stop, "
                     f"{c.get('en_curso', 0) + c.get('sin_entrada', 0)} ⏳ en curso")
    operated = int(recent["senal"].notna().sum()) if "senal" in recent else 0
    return (f"De {len(recent)} horas analizadas, la {parts[0]}; la {parts[1]}. "
            f"El sistema operó en {operated} de ellas (las que llegaron al umbral).")


def live_calibration(resolved: pd.DataFrame) -> List[List[str]]:
    """
    Acumulado desde que se guardan evaluaciones: por lado y tramo de
    probabilidad, cuántas horas ya tienen resultado y qué % tocó el objetivo.
    """
    rows = []
    for side, name in _SIDE_NAMES.items():
        if resolved.empty:
            break
        done = resolved[resolved[f"res_{side}"].isin(["tp", "sl", "ninguno"])]
        p = done[f"p_{side}"].astype(float)
        for lo, hi, label in LIVE_BANDS:
            sub = done[(p >= lo) & (p < hi)]
            if sub.empty:
                continue
            tp = (sub[f"res_{side}"] == "tp").mean()
            rows.append([name, label, f"{len(sub)}", f"{p[(p >= lo) & (p < hi)].mean() * 100:.0f} %",
                         f"{tp * 100:.0f} %"])
    return rows


def hour_rows(recent: pd.DataFrame, digits: int, fmt_hora, gran_minutes: int = 60) -> List[List[str]]:
    """
    Una fila por vela evaluada: hora en que cerró (cuando el modelo decidió), precio,
    compra y venta (probabilidad y resultado) y si operó.
    """
    side_names = {"buy": "COMPRA", "sell": "VENTA"}
    rows = []
    for ev in recent.to_dict("records"):
        def cell(side):
            p = ev.get(f"p_{side}")
            prob = f"{float(p) * 100:.0f} %" if p is not None and not pd.isna(p) else "-"
            return f"{prob} · {RESULT_TEXT.get(ev[f'res_{side}'], ev[f'res_{side}'])}"
        close = ev.get("close")
        senal = ev.get("senal")
        rows.append([fmt_hora(pd.Timestamp(ev["timestamp"]) + timedelta(minutes=gran_minutes)),
                     f"{float(close):.{digits}f}" if close is not None and not pd.isna(close) else "-",
                     cell("long"), cell("short"),
                     side_names.get(str(senal).lower(), "") if senal and not pd.isna(senal) else ""])
    return rows
