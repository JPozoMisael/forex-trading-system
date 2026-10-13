"""
Monitor de salud e infraestructura de los microservicios y dependencias.
"""
import time
from datetime import datetime, timezone
from typing import Dict, Any
import psycopg2

from shared.config import settings
from shared.logger import get_logger
from shared.bus import EventBus

log = get_logger(__name__)


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

    def run_full_check(self) -> Dict[str, Any]:
        pg = self.check_postgres()
        rd = self.check_redis()
        br = self.check_broker()
        all_ok = all(x.get("status") == "healthy" for x in (pg, rd, br))
        return {
            "overall_status": "UP" if all_ok else "DEGRADED",
            "postgres": pg,
            "redis": rd,
            "broker": br,
        }

    def close(self):
        self.bus.close()
