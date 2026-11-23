"""Correo de señal con gráfico: soportes, resistencias, techo/piso de 24 h y anatomía de la vela."""
import pandas as pd
import pytest

from data_loader import generate_synthetic_forex_data
from signal_chart import build_signal_email, candle_anatomy, key_levels, level_lines, send_signal_report


def _candles(n=300):
    df = generate_synthetic_forex_data(pair="EUR_USD", n_candles=n, granularity_minutes=60, seed=7)
    df = df[["open", "high", "low", "close"]]
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df


def _sig(df, buy=True):
    close = float(df["close"].iloc[-1])
    s = 1 if buy else -1
    return {"pair": "EUR_USD", "direction": "buy" if buy else "sell", "strategy_name": "practica",
            "confidence": 0.44, "entry_price": close, "stop_loss": close - s * 0.002,
            "take_profit": close + s * 0.003, "metadata": {"umbral": 0.5, "por_que": []}}


def test_levels_are_on_the_right_side_of_price():
    lv = key_levels(_candles())
    close = lv["cierre"]
    assert lv["piso_24h"] <= close <= lv["techo_24h"]
    assert all(z["precio"] > close and z["toques"] >= 1 for z in lv["resistencias"])
    assert all(z["precio"] < close for z in lv["soportes"])
    assert lv["resistencias"] == sorted(lv["resistencias"], key=lambda z: z["precio"])  # la más cercana primero


def test_candle_anatomy_detects_rejection():
    # Martillo: cuerpo chico arriba, mecha inferior larga
    an = candle_anatomy(pd.Series({"open": 1.1000, "high": 1.1003, "low": 1.0970, "close": 1.1002}), 0.0015)
    assert an["alcista"] and an["mecha_inf"] == pytest.approx(0.0030)
    assert any("mecha inferior larga" in n for n in an["notas"])
    flat = candle_anatomy(pd.Series({"open": 1.1, "high": 1.1006, "low": 1.0994, "close": 1.1001}), 0.0015)
    assert any("indecisión" in n for n in flat["notas"])


def test_level_lines_warn_about_resistance_before_target():
    lv = {"cierre": 1.1000, "atr": 0.001, "techo_24h": 1.1030, "piso_24h": 1.0980,
          "resistencias": [{"precio": 1.1015, "toques": 3}], "soportes": [{"precio": 1.0985, "toques": 2}]}
    sig = {"direction": "buy", "take_profit": 1.1030}
    text = "\n".join(level_lines("EUR_USD", sig, lv))
    assert "Resistencia: 1.10150 (3 toque(s), 15.0 pips arriba)" in text
    assert "Ojo: antes del objetivo hay una resistencia en 1.10150" in text
    clear = "\n".join(level_lines("EUR_USD", {"direction": "buy", "take_profit": 1.1010}, lv))
    assert "Camino al objetivo: ninguna resistencia marcada" in clear and "Ojo" not in clear


def test_signal_email_has_chart_and_guide():
    df = _candles()
    subject, html, text, images = build_signal_email(_sig(df), df, "H1")
    assert subject.startswith("[Señal] EUR_USD COMPRA · práctica")
    assert images["senal"][:4] == b"\x89PNG"
    assert 'src="cid:senal"' in html and "Cómo leer el gráfico" in html and "Mechas" in html
    assert "Techo de 24 h" in text and "Última vela" in text and "POR QUÉ" in text


def test_without_candles_sends_text_only():
    class Mail:
        def send_html(self, subject, html, text, images=None):
            self.sent = (subject, images)
            return True

    m = Mail()
    assert send_signal_report(m, _sig(_candles()), fetch=lambda *a: pd.DataFrame())
    assert m.sent[1] == {} and "POR QUÉ" not in m.sent[0]
