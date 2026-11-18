"""
Resumen de noticias del reporte: lectura de los RSS, llamada a Claude (con un
cliente falso: los tests no gastan API) y la sección del reporte.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from news_digest import _parse_date, digest_enabled, fetch_headlines, news_for_report, summarize

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Trump arrives in China for meeting with Xi</title><pubDate>Wed, 18 Nov 2026 12:24:24 GMT</pubDate></item>
<item><title>Fed holds rates</title><pubDate>2026-11-18 13:19:01</pubDate></item>
<item><title>Old news</title><pubDate>Mon, 16 Nov 2026 10:00:00 GMT</pubDate></item>
<item><title>Trump arrives in China for meeting with Xi</title><pubDate>Wed, 18 Nov 2026 12:30:00 GMT</pubDate></item>
</channel></rss>"""


class _Resp:
    def __init__(self, content=RSS, fail=False):
        self.content, self.fail = content, fail

    def raise_for_status(self):
        if self.fail:
            raise RuntimeError("503")


def test_dates_in_both_formats():
    assert _parse_date("Wed, 18 Nov 2026 12:24:24 GMT") == datetime(2026, 11, 18, 12, 24, 24, tzinfo=timezone.utc)
    assert _parse_date("2026-11-18 13:19:01") == datetime(2026, 11, 18, 13, 19, 1, tzinfo=timezone.utc)
    assert _parse_date("ayer") is None


def test_headlines_last_24h_without_duplicates_and_failed_feeds_skipped():
    def get(url, **kw):
        return _Resp(fail="caida" in url)

    hs = fetch_headlines(NOW, feeds=["https://www.fxstreet.com/rss", "https://caida.com/rss"], get=get)
    assert [h["titulo"] for h in hs] == ["Fed holds rates", "Trump arrives in China for meeting with Xi"]
    assert hs[0]["fuente"] == "fxstreet.com"


class _FakeClient:
    def __init__(self, payload, stop="end_turn"):
        self.calls = []
        text = json.dumps(payload)
        create = lambda **kw: (self.calls.append(kw) or SimpleNamespace(
            stop_reason=stop, content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)]))
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


PAYLOAD = {"noticias": [{"titulo": "Trump se reúne con Xi <y Putin>", "explicacion": "Menos tensión comercial.",
                         "efectos": [{"instrumento": "XAU_USD", "direccion": "baja"},
                                     {"instrumento": "EUR_USD", "direccion": "sube"}]}],
           "resumen": "Mercado tranquilo."}


def test_summarize_asks_for_structured_json_with_fallback():
    client = _FakeClient(PAYLOAD)
    hs = fetch_headlines(NOW, feeds=["https://www.fxstreet.com/rss"], get=lambda u, **k: _Resp())
    out = summarize(hs, ["EUR_USD", "XAU_USD"], client=client)
    assert out["resumen"] == "Mercado tranquilo." and out["titulares"] == 2
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    schema = call["output_config"]["format"]["schema"]
    assert schema["properties"]["noticias"]["items"]["properties"]["efectos"]["items"]["properties"][
        "instrumento"]["enum"] == ["EUR_USD", "XAU_USD"]
    assert "Fed holds rates" in call["messages"][0]["content"]  # los titulares van como datos


def test_refusal_or_no_headlines_give_none():
    hs = [{"titulo": "x", "cuando": NOW, "fuente": "a"}]
    assert summarize(hs, ["EUR_USD"], client=_FakeClient(PAYLOAD, stop="refusal")) is None
    assert summarize([], ["EUR_USD"], client=_FakeClient(PAYLOAD)) is None
    # Un error inesperado no tumba el reporte
    def boom(now):
        raise RuntimeError("x")
    assert news_for_report(["EUR_USD"], NOW, fetch=boom) is None


def test_disabled_without_api_key():
    assert not digest_enabled()  # en los tests no hay ANTHROPIC_API_KEY


def test_report_section_is_escaped():
    from test_practice import _data
    from daily_report import build_report

    d = _data("EUR_USD", 0.41, 0.33)
    _, html, text = build_report([d], news={**PAYLOAD, "titulares": 40}, news_on=True)
    assert "🌍 Lo que está moviendo el mundo" in html
    assert "Trump se reúne con Xi &lt;y Putin&gt;" in html and "<y Putin>" not in html
    assert "[Oro (XAU/USD) ↓, EUR/USD ↑]" in text and "Se leyeron 40 titulares" in html
    _, html_fail, _ = build_report([d], news=None, news_on=True)
    assert "No se pudo preparar el resumen de noticias" in html_fail
    _, html_off, _ = build_report([d])
    assert "moviendo el mundo" not in html_off
