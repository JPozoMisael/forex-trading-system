"""
Target de triple barrera, con las mismas reglas de ejecución que el backtester.

Para la vela t (cerrada): se entra a la APERTURA de t+1 pagando medio spread y
slippage, con SL y TP a `sl_atr` y `tp_atr` veces el ATR de t (los mismos que
usará la operación). Se recorren las velas t+1 .. t+horizonte:
  - 1 si toca el TP antes que el SL,
  - 0 si toca primero el SL, si ambos caen en la misma vela (se asume SL,
    igual que el backtester) o si no toca ninguno dentro del horizonte.
Las últimas `horizonte` velas no tienen futuro completo: su etiqueta es NaN y
se descartan. Por eso, entrenar con datos hasta T nunca usa precios de
después de T: el hueco entre entrenamiento y prueba es el propio horizonte.
"""
import numpy as np
import pandas as pd

from shared.fx import get_pip_size


def triple_barrier_labels(
    df: pd.DataFrame,
    atr: pd.Series,
    horizon: int,
    sl_atr: float,
    tp_atr: float,
    pair: str,
    spread_pips: float = 0.0,
    slippage_pips: float = 0.0,
) -> pd.DataFrame:
    pip = get_pip_size(pair)
    hs, slip = spread_pips * pip / 2, slippage_pips * pip
    o, h, l = (df[k].to_numpy(dtype=float) for k in ("open", "high", "low"))
    a = atr.to_numpy(dtype=float)
    n = len(df)

    entry_open = np.full(n, np.nan)
    entry_open[:-1] = o[1:]
    long_entry = entry_open + hs + slip
    short_entry = entry_open - hs - slip
    long_sl, long_tp = long_entry - sl_atr * a, long_entry + tp_atr * a
    short_sl, short_tp = short_entry + sl_atr * a, short_entry - tp_atr * a

    never = horizon + 1
    first = {k: np.full(n, never) for k in ("long_sl", "long_tp", "short_sl", "short_tp")}
    for k in range(1, horizon + 1):
        hk = np.full(n, np.nan)
        lk = np.full(n, np.nan)
        hk[:-k] = h[k:]
        lk[:-k] = l[k:]
        hits = {
            "long_sl": (lk - hs) <= long_sl,   # el BUY cierra al bid
            "long_tp": (hk - hs) >= long_tp,
            "short_sl": (hk + hs) >= short_sl,  # el SELL cierra al ask
            "short_tp": (lk + hs) <= short_tp,
        }
        for name, hit in hits.items():
            newly = hit & (first[name] == never)
            first[name][newly] = k

    long_win = (first["long_tp"] < first["long_sl"]) & (first["long_tp"] <= horizon)
    short_win = (first["short_tp"] < first["short_sl"]) & (first["short_tp"] <= horizon)

    labels = pd.DataFrame({"long": long_win.astype(float), "short": short_win.astype(float)}, index=df.index)
    incomplete = np.arange(n) >= n - horizon
    labels.loc[incomplete | np.isnan(a), :] = np.nan
    return labels
