"""
Monitor de salud e infraestructura de los microservicios y dependencias.

Además de PostgreSQL, Redis y el snapshot de cuenta, vigila la cadena de
datos: que el data-collector traiga velas nuevas (solo con el mercado
abierto) y que el strategy-engine evalúe la última vela.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import psycopg2

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus
from shared.timefmt import fmt_local

log = get_logger(__name__)

# Componentes que cuentan para el estado general. "n/a" (mercado cerrado,
# sin datos todavía) no es un fallo.
COMPONENTS = ("postgres", "redis", "broker", "datos", "estrategia")


def market_open(now: datetime) -> bool:
    """Forex abre el domingo ~22:00 UTC y cierra el viernes ~21:00 UTC."""
    wd, h = now.weekday(), now.hour
    if wd == 5:  # sábado
        return False
    if wd == 4 and h >= 21:
        return False
    if wd == 6 and h < 22:
        return False
    return True


def candle_status(last_candle: Optional[datetime], now: datetime, gran_minutes: int) -> Dict[str, Any]:
    """¿Llegan velas nuevas? Tolerancia: una vela y media de retraso sobre lo esperado."""
    # Recién abierto el mercado aún no hay vela nueva: no se exige nada
    if not (market_open(now) and market_open(now - timedelta(hours=2))):
        return {"status": "n/a", "detail": "mercado cerrado"}
    if last_candle is None:
        return {"status": "unhealthy", "detail": "no hay velas en la BD"}
    bar = timedelta(minutes=gran_minutes)
    lag = now - (last_candle + bar)  # tiempo desde que cerró la última vela guardada
    if lag > bar * 1.5:
        return {"status": "unhealthy",
                "detail": f"la última vela cerró el {fmt_local(last_candle + bar)}; sin velas nuevas hace {int(lag.total_seconds() // 60)} min"}
    return {"status": "healthy", "detail": f"última vela cerró el {fmt_local(last_candle + bar)}"}


def strategy_status(state: Optional[Dict[str, Any]], last_candle: Optional[datetime], now: datetime,
                    gran_minutes: int, grace_minutes: int = 15) -> Dict[str, Any]:
    """¿El strategy-engine evaluó la última vela guardada?"""
    if not state or last_candle is None:
        return {"status": "n/a", "detail": "sin evaluaciones registradas todavía"}
    evaluated = datetime.fromisoformat(state["vela"]) if isinstance(state["vela"], str) else state["vela"]
    closed_at = last_candle + timedelta(minutes=gran_minutes)
    if evaluated < last_candle and now - closed_at > timedelta(minutes=grace_minutes):
        return {"status": "unhealthy",
                "detail": f"no analizó la vela que cerró el {fmt_local(closed_at)} (la última analizada es la que abrió el {fmt_local(evaluated)})"}
    errors = [name for name, s in (state.get("estrategias") or {}).items() if s.get("error")]
    if errors:
        return {"status": "unhealthy", "detail": f"error al evaluar {', '.join(errors)}"}
    return {"status": "healthy", "detail": f"analizó la vela que cerró el {fmt_local(evaluated + timedelta(minutes=gran_minutes))}"}


def combine_status(per_pair: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Un estado para varios pares: falla si falla alguno (y dice cuál)."""
    bad = {p: s for p, s in per_pair.items() if s["status"] == "unhealthy"}
    if bad:
        return {"status": "unhealthy", "detail": "; ".join(f"{p}: {s['detail']}" for p, s in bad.items())}
    if per_pair and all(s["status"] == "n/a" for s in per_pair.values()):
        return {"status": "n/a", "detail": next(iter(per_pair.values()))["detail"]}
    ok = [p for p, s in per_pair.items() if s["status"] == "healthy"]
    detail = next((s["detail"] for s in per_pair.values() if s["status"] == "healthy"), "")
    return {"status": "healthy", "detail": f"{detail} ({len(ok)} par(es))" if len(per_pair) > 1 else detail}


def overall_status(report: Dict[str, Any]) -> str:
    return "DEGRADED" if any(report[c].get("status") == "unhealthy" for c in COMPONENTS if c in report) else "UP"


def failure_message(report: Dict[str, Any]) -> str:
    names = {"postgres": "Base de datos", "redis": "Redis", "broker": "Execution-engine / cuenta",
             "datos": "Data-collector (velas)", "estrategia": "Strategy-engine (modelo)"}
    lines = [f"- {names[c]}: {report[c].get('detail') or report[c].get('error') or 'fallo'}"
             for c in COMPONENTS if report.get(c, {}).get("status") == "unhealthy"]
    return "\n".join(lines)


class HealthChecker:
    def __init__(self):
        self.bus = EventBus()

    def check_postgres(self) -> Dict[str, Any]:
        start = time.time()
        try:
            conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=3)
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                cur.fetchone()
            conn.close()
            latency_ms = round((time.time() - start) * 1000, 2)
            return {"status": "healthy", "latency_ms": latency_ms}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def check_redis(self) -> Dict[str, Any]:
        start = time.time()
        try:
            ok = self.bus.ping()
            latency_ms = round((time.time() - start) * 1000, 2)
            return {"status": "healthy" if ok else "unhealthy", "latency_ms": latency_ms}
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    def check_broker(self) -> Dict[str, Any]:
        """
        El execution-engine publica el snapshot de cuenta cada 5 s. Si está viejo,
        el execution-engine o MT5 están caídos y el risk-manager rechazará todo.
        """
        mode = "simulation" if settings.dry_run else f"mt5:{settings.mt5_server}"
        try:
            snap = self.bus.get_json(settings.key_account_snapshot)
        except Exception as e:
            return {"status": "unhealthy", "mode": mode, "detail": f"Redis: {e}"}
        if not snap:
            return {"status": "unhealthy", "mode": mode, "detail": "sin snapshot de cuenta"}
        ts = datetime.fromisoformat(snap["timestamp"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        if age > settings.account_snapshot_max_age_seconds:
            return {"status": "unhealthy", "mode": mode, "detail": f"snapshot con {int(age)}s de antigüedad"}
        return {
            "status": "healthy",
            "mode": mode,
            "detail": f"balance {snap.get('balance')} / equity {snap.get('equity')} {snap.get('currency', '')}",
        }

    def last_candle(self, pair: str) -> Optional[datetime]:
        conn = psycopg2.connect(settings.postgres_dsn, connect_timeout=3)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT max(timestamp) FROM candles WHERE pair = %s AND granularity = %s AND complete;",
                            (pair, settings.candle_granularity))
                return cur.fetchone()[0]
        finally:
            conn.close()

    def check_pipeline(self, now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
        """Velas nuevas y evaluación del modelo en cada par de FOREX_PAIRS."""
        now = now or datetime.now(timezone.utc)
        gran = settings.granularity_minutes
        per_pair = {}
        for pair in settings.forex_pairs:
            try:
                last = self.last_candle(pair)
            except Exception as e:
                err = {"status": "n/a", "detail": f"BD no disponible: {e}"}
                return {"datos": err, "estrategia": err}
            try:
                state = self.bus.get_json(f"{settings.key_strategy_state_prefix}{pair}")
            except Exception:
                state = None
            per_pair[pair] = (candle_status(last, now, gran), strategy_status(state, last, now, gran))
        return {"datos": combine_status({p: s[0] for p, s in per_pair.items()}),
                "estrategia": combine_status({p: s[1] for p, s in per_pair.items()})}

    def run_full_check(self) -> Dict[str, Any]:
        report = {"postgres": self.check_postgres(), "redis": self.check_redis(), "broker": self.check_broker()}
        report.update(self.check_pipeline())
        report["overall_status"] = overall_status(report)
        return report

    def close(self):
        self.bus.close()
