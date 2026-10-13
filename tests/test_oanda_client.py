"""
Pruebas del cliente de datos OANDA con la API simulada (sin red).
"""
from datetime import datetime, timezone

from oanda_client import OandaClient, _parse_oanda_time


class FakeAPI:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def request(self, endpoint):
        self.requests.append(endpoint)
        return self.response


def candle(t, c, complete=True):
    return {"time": t, "complete": complete, "volume": 10, "mid": {"o": c, "h": c, "l": c, "c": c}}


def test_parse_nanosecond_timestamps():
    assert _parse_oanda_time("2026-10-13T12:05:00.123456789Z") == datetime(2026, 10, 13, 12, 5, 0, 123456, tzinfo=timezone.utc)


def test_connect_fails_without_credentials():
    assert OandaClient(api_key="", account_id="").connect() is False


def test_connect_fails_with_invalid_environment():
    assert OandaClient(api_key="x", account_id="y", environment="demo").connect() is False


def test_candles_skip_forming_bar_and_respect_count():
    client = OandaClient(api_key="x", account_id="y")
    client._api = FakeAPI({"candles": [
        candle("2026-10-13T12:00:00.000000000Z", "1.1000"),
        candle("2026-10-13T12:05:00.000000000Z", "1.1010"),
        candle("2026-10-13T12:10:00.000000000Z", "1.1020"),
        candle("2026-10-13T12:15:00.000000000Z", "1.1030", complete=False),
    ]})
    candles = client.get_latest_candles("EUR_USD", "M5", count=2)
    assert [c.close for c in candles] == [1.1010, 1.1020]
    assert candles[-1].timestamp == datetime(2026, 10, 13, 12, 10, tzinfo=timezone.utc)
    assert client._api.requests[0].params["count"] == 3  # pide una de más por la vela en formación
