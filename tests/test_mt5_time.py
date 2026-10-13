"""
Conversión de la hora del servidor MT5 (XM: UTC+2 invierno / UTC+3 verano) a UTC.
"""
from datetime import datetime, timezone

from shared.mt5_client import _us_dst_active, server_epoch_to_utc


def test_us_dst_boundaries_2026():
    # 2026: DST de EE.UU. del 8 de marzo al 1 de noviembre
    assert not _us_dst_active(datetime(2026, 3, 8, 6, 59, tzinfo=timezone.utc))
    assert _us_dst_active(datetime(2026, 3, 8, 7, 0, tzinfo=timezone.utc))
    assert _us_dst_active(datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc))
    assert not _us_dst_active(datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc))


def test_server_time_to_utc_summer_and_winter():
    # Verano: vela de las 15:00 hora servidor (UTC+3) = 12:00 UTC
    summer_server = int(datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc).timestamp())
    assert server_epoch_to_utc(summer_server) == datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    # Invierno: 15:00 hora servidor (UTC+2) = 13:00 UTC
    winter_server = int(datetime(2026, 1, 15, 15, 0, tzinfo=timezone.utc).timestamp())
    assert server_epoch_to_utc(winter_server) == datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)
