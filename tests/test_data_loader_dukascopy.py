"""
Pruebas del cargador de Dukascopy con una librería falsa (sin internet):
precio medio a partir de bid/ask, solo velas cerradas y caché incremental.
"""
import sys
import types
from datetime import datetime, timezone

import pandas as pd
import pytest

from data_loader import dukascopy_cache_path, load_from_dukascopy


class FakeDukascopy(types.ModuleType):
    """Imita dukascopy_python.fetch: velas H1 con ask = bid + 0.0002 (2 pips)."""

    INTERVAL_HOUR_1 = "1HOUR"
    OFFER_SIDE_BID = "B"
    OFFER_SIDE_ASK = "A"

    def __init__(self):
        super().__init__("dukascopy_python")
        self.calls = []

    def fetch(self, instrument, interval, offer_side, start, end):
        self.calls.append((instrument, offer_side, start, end))
        idx = pd.date_range(pd.Timestamp(start).ceil("h"), pd.Timestamp(end), freq="h", inclusive="left", tz="UTC")
        base = pd.Series(range(len(idx)), index=idx, dtype=float) * 0.0001 + 1.1000
        add = 0.0002 if offer_side == self.OFFER_SIDE_ASK else 0.0
        return pd.DataFrame(
            {"open": base + add, "high": base + 0.0005 + add, "low": base - 0.0005 + add, "close": base + add, "volume": 10.0},
            index=idx,
        )


@pytest.fixture
def fake_dk(monkeypatch):
    fake = FakeDukascopy()
    monkeypatch.setitem(sys.modules, "dukascopy_python", fake)
    return fake


NOW = datetime(2026, 1, 7, 10, 30, tzinfo=timezone.utc)


def test_mid_price_and_spread_from_bid_ask(fake_dk, tmp_path):
    df = load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=NOW)
    assert fake_dk.calls[0][0] == "EUR/USD"
    first = df.iloc[0]
    # Medio = bid + 1 pip (la mitad de los 2 pips de spread)
    assert first["close"] == pytest.approx(1.1000 + 0.0001)
    assert df["spread_pips"].round(6).eq(2.0).all()


def test_only_closed_candles_are_returned(fake_dk, tmp_path):
    df = load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=NOW)
    # A las 10:30 la vela de las 10:00 sigue abierta: la última cerrada es la de las 09:00
    assert df.index[-1] == pd.Timestamp("2026-01-07 09:00", tz="UTC")


def test_cache_is_saved_and_updated_incrementally(fake_dk, tmp_path):
    first = load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=NOW)
    assert dukascopy_cache_path("EUR_USD", "H1", tmp_path).exists()

    fake_dk.calls.clear()
    later = datetime(2026, 1, 7, 15, 5, tzinfo=timezone.utc)
    updated = load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=later)
    # Solo pide desde la última vela guardada, no toda la historia
    assert all(start >= first.index[-1].to_pydatetime() for _, _, start, _ in fake_dk.calls)
    assert updated.index[-1] == pd.Timestamp("2026-01-07 14:00", tz="UTC")
    assert not updated.index.has_duplicates
    assert updated.index.is_monotonic_increasing


def test_cache_is_extended_backwards_when_more_years_are_requested(fake_dk, tmp_path):
    short = load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=NOW)
    longer = load_from_dukascopy("EUR_USD", "H1", years=0.05, data_dir=tmp_path, now=NOW)
    assert longer.index[0] < short.index[0]
    assert longer.index[-1] == short.index[-1]
    assert not longer.index.has_duplicates
    assert longer.index.is_monotonic_increasing


def test_refresh_false_reads_cache_without_network(fake_dk, tmp_path):
    load_from_dukascopy("EUR_USD", "H1", years=0.01, data_dir=tmp_path, now=NOW)
    fake_dk.calls.clear()
    cached = load_from_dukascopy("EUR_USD", "H1", refresh=False, data_dir=tmp_path)
    assert fake_dk.calls == []
    assert len(cached) > 0


def test_unsupported_granularity_raises(tmp_path):
    with pytest.raises(ValueError):
        load_from_dukascopy("EUR_USD", "S5", data_dir=tmp_path)
