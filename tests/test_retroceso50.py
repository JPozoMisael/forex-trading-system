"""
Método "retroceso al 50 %" (config.yaml, retroceso_50): orden limitada en el 50 %
del impulso, stop bajo el 61.8 %, objetivo en el extremo, costes del oro.
"""
import numpy as np
import pandas as pd
import pytest

from retroceso50 import Params, impulses, simulate

P = Params(ventana_impulso=48, impulso_min_atr=10, validez_orden_velas=36, max_velas_abierta=48)
HS, SLIP, COMM = 0.25, 0.10, 0.07  # oro: medio spread, slippage y comisión por onza


def _bars(closes, wick=0.3):
    idx = pd.date_range("2026-11-04 10:00", periods=len(closes), freq="5min", tz="UTC")
    c = np.asarray(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) + wick, "low": np.minimum(o, c) - wick, "close": c},
                        index=idx)


def _friend_like(after):
    """Quieto en 4040, impulso a 4071, retroceso hasta ~4055 y luego `after`."""
    quiet = [4040.0] * 60
    up = list(np.linspace(4040, 4070.7, 8))
    down = list(np.linspace(4069, 4056.5, 20))
    return _bars(quiet + up + down + after)


def test_buys_the_50_percent_and_reaches_the_high():
    df = _friend_like(list(np.linspace(4055.0, 4072, 15)))
    t = simulate(df, "XAU_USD", P)
    first = t.iloc[0]
    assert first["side"] == "buy" and first["exit_reason"] == "tp"
    hi, lo = 4071.0, 4039.7  # máximo y mínimo con mechas
    assert first["entry_price"] == pytest.approx(lo + 0.5 * (hi - lo))
    assert first["take_profit"] == pytest.approx(hi)
    risk = first["entry_price"] - first["stop_loss"]
    expected_r = ((hi - SLIP) - first["entry_price"] - COMM) / risk
    assert first["r"] == pytest.approx(expected_r) and first["r"] > 3   # ~1:4 como el amigo


def test_stop_loss_when_the_pullback_keeps_falling():
    df = _friend_like(list(np.linspace(4055.0, 4035, 15)))
    first = simulate(df, "XAU_USD", P).iloc[0]
    assert first["side"] == "buy" and first["exit_reason"] == "sl"
    assert first["r"] == pytest.approx((first["stop_loss"] - SLIP - first["entry_price"] - COMM)
                                       / (first["entry_price"] - first["stop_loss"]))
    assert first["r"] < -1  # un poco peor que -1 R por slippage y comisión


def test_order_is_cancelled_if_price_makes_a_new_high_first():
    quiet = [4040.0] * 60
    df = _bars(quiet + list(np.linspace(4040, 4070.7, 8)) + [4075, 4080, 4085])  # sigue subiendo, no retrocede
    t = simulate(df, "XAU_USD", P)
    assert t.empty or (t["entry_price"] > 4060).all()  # nunca se llenó la orden del primer impulso


def test_small_moves_are_not_impulses():
    df = _bars([4040 + np.sin(i / 3) for i in range(200)])
    assert (impulses(df, P)["dir"] == 0).all()


def test_impulses_never_look_ahead():
    rng = np.random.default_rng(3)
    df = _bars(4000 + np.cumsum(rng.normal(0, 1.5, 3000)))
    full = impulses(df, P)
    altered = df.copy()
    altered.iloc[2000:, :] += 50
    pd.testing.assert_frame_equal(full.iloc[:2000], impulses(altered, P).iloc[:2000])


def test_period_stats_and_email_for_any_user():
    from run_retroceso50 import email_html, period_stats

    val = {"n_permutaciones": 200, "min_operaciones": 2, "expectativa_minima_r": 0.05}
    idx = pd.date_range("2024-01-01", periods=4, freq="7D", tz="UTC")
    trades = pd.DataFrame({"r": [4.0, -1.1, -1.1, 3.9], "r_opposite": [-1.1, 0.5, 0.4, -1.1], "entry_time": idx})
    s = period_stats(trades, val)
    assert s["n"] == 4 and s["win"] == 0.5 and s["racha"] == 2
    assert s["ret"] == pytest.approx((1.04 * 0.989 * 0.989 * 1.039 - 1) * 100)
    df = _friend_like(list(np.linspace(4055.0, 4072, 15)))
    recent = simulate(df, "XAU_USD", P)
    subject, html, text = email_html(s, s, recent, ["operacion_01.png"], {"validacion": {}})
    assert "¿Funciona el método de tu amigo en el oro?" in html and "cid:operacion_01" in html
    assert "llegó al objetivo" in html and "+$" in html  # la operación de ejemplo, en dinero de la cuenta de $10,000


def test_opposite_trade_is_the_mirror():
    df = _friend_like(list(np.linspace(4055.0, 4072, 15)))
    first = simulate(df, "XAU_USD", P).iloc[0]
    assert first["r_opposite"] < 0  # vender en ese momento habría perdido
