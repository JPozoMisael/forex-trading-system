"""
Horas para las personas: todo el sistema trabaja en UTC, pero los reportes y
avisos se muestran en la hora local del usuario (REPORT_UTC_OFFSET_HOURS,
por defecto -5 = Ecuador, sin horario de verano).
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from shared.config import settings

_DIAS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")


def local_tz() -> timezone:
    return timezone(timedelta(hours=settings.report_utc_offset_hours), settings.report_timezone_name)


def to_local(dt: Any) -> datetime:
    """datetime, pandas.Timestamp o texto ISO (UTC si no trae zona) -> hora local."""
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
    if hasattr(dt, "to_pydatetime"):
        dt = dt.to_pydatetime()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(local_tz())


def fmt_hora(dt: Any) -> str:
    """'5:00 a. m.'"""
    d = to_local(dt)
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'a. m.' if d.hour < 12 else 'p. m.'}"


def fmt_local(dt: Any, fecha: bool = True) -> str:
    """'mié 28/10 5:00 a. m.' (o solo la hora con fecha=False)."""
    if dt is None:
        return "-"
    d = to_local(dt)
    return f"{_DIAS[d.weekday()]} {d:%d/%m} {fmt_hora(d)}" if fecha else fmt_hora(d)


def fmt_fecha(dt: Any) -> str:
    """'mié 28/10'"""
    d = to_local(dt)
    return f"{_DIAS[d.weekday()]} {d:%d/%m}"


def zona() -> str:
    """'hora de Ecuador'"""
    return f"hora de {settings.report_timezone_name}"
