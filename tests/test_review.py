"""
Repaso del reporte: qué habría pasado con la compra y la venta de cada hora,
con las mismas reglas que el objetivo del modelo.
"""
from datetime import datetime, timezone

import pandas as pd
import pytest

from review import hour_rows, last_hours, live_calibration, resolve, resolve_all, summary_text

T0 = pd.Timestamp("2026-11-02 10:00", tz="UTC")


def _bars(rows):
    idx = pd.date_range(T0 + pd.Timedelta(hours=1), periods=len(rows), freq="h")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)


def _ev(**kw):
    ev = {"timestamp": T0, "p_long": 0.42, "p_short": 0.31, "umbral": 0.5, "close": 1.1000, "atr": 0.0010,
          "sl_atr": 2.0, "tp_atr": 3.0, "horizonte_velas": 4, "senal": None}
    ev.update(kw)
    return ev


# EUR_USD: medio spread 0.5 pip + slippage 0.3 → compra entra a 1.10008; SL 1.09808; TP 1.10308
def test_buy_reaches_target_and_sell_its_stop():
    candles = _bars([(1.1000, 1.1010, 1.0995, 1.1008), (1.1008, 1.1040, 1.1005, 1.1035)])
    r = resolve(_ev(), candles, "EUR_USD")
    assert r["long"]["entry"] == pytest.approx(1.10008) and r["long"]["res"] == "tp"
    assert r["short"]["res"] == "sl"


def test_both_in_the_same_candle_counts_as_stop():
    candles = _bars([(1.1000, 1.1040, 1.0970, 1.1000)])
    r = resolve(_ev(), candles, "EUR_USD")
    assert r["long"]["res"] == "sl" and r["short"]["res"] == "sl"


def test_still_running_none_and_no_entry_yet():
    quiet = (1.1000, 1.1005, 1.0995, 1.1000)
    assert resolve(_ev(), _bars([quiet] * 2), "EUR_USD")["long"]["res"] == "en_curso"   # 2 de 4 velas
    assert resolve(_ev(), _bars([quiet] * 6), "EUR_USD")["long"]["res"] == "ninguno"    # pasó el horizonte
    assert resolve(_ev(), _bars([quiet]).iloc[:0], "EUR_USD")["long"]["res"] == "sin_entrada"
    assert resolve(_ev(atr=None), _bars([quiet]), "EUR_USD")["long"]["res"] == "sin_entrada"


def test_summary_rows_and_live_calibration():
    candles = _bars([(1.1000, 1.1010, 1.0995, 1.1008), (1.1008, 1.1040, 1.1005, 1.1035)] + [(1.1035,) * 4] * 4)
    evals = pd.DataFrame([_ev(), _ev(timestamp=T0 - pd.Timedelta(days=3), p_long=0.55, senal="buy")])
    resolved = resolve_all(evals, candles, "EUR_USD")
    recent = last_hours(resolved, datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc))
    assert len(recent) == 1  # la de hace 3 días no entra en las últimas 24 h
    assert summary_text(recent).startswith("De 1 horas analizadas, la compra: 1 ✓ objetivo, 0 ✗ stop")
    row = hour_rows(recent, 5, lambda ts: ts.strftime("%H:%M"))[0]
    assert row == ["11:00", "1.10000", "42 % · ✓ objetivo", "31 % · ✗ stop", ""]
    live = live_calibration(resolved)
    assert ["Compra", "40–50 %", "1", "42 %", "100 %"] in live
