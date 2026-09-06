"""
Monitor de salud e infraestructura de los microservicios y dependencias.
"""
import time
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

    def check_oanda_config(self) -> Dict[str, Any]:
        has_key = bool(settings.oanda_api_key and settings.oanda_api_key != "tu_api_key_aqui")
        return {
            "environment": settings.oanda_environment,
            "has_credentials": has_key,
            "mode": "live" if has_key and not settings.dry_run else "simulation",
        }

    def run_full_check(self) -> Dict[str, Any]:
        pg = self.check_postgres()
        rd = self.check_redis()
        oa = self.check_oanda_config()
        all_ok = (pg.get("status") == "healthy") and (rd.get("status") == "healthy")
        return {
            "overall_status": "UP" if all_ok else "DEGRADED",
            "postgres": pg,
            "redis": rd,
            "broker": oa,
        }

    def close(self):
        self.bus.close()
