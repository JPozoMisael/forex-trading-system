"""
Inserta velas en TimescaleDB, evitando duplicados con ON CONFLICT.
"""
from typing import List, Optional
import psycopg2
from psycopg2.extras import execute_values

from shared.config import settings
from shared.logger import get_logger
from shared.models import Candle

log = get_logger(__name__)


class DBWriter:
    def __init__(self, dsn: Optional[str] = None):
        self.dsn = dsn or settings.postgres_dsn
        self._conn: Optional[psycopg2.extensions.connection] = None

    def _get_connection(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            log.info(f"Conectando a base de datos PostgreSQL/TimescaleDB en {settings.postgres_host}:{settings.postgres_port}...")
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = False
        return self._conn

    def write_candles(self, candles: List[Candle]) -> int:
        if not candles:
            return 0

        try:
            conn = self._get_connection()
            rows = [
                (c.pair, c.granularity, c.timestamp, c.open, c.high, c.low, c.close, c.volume, c.complete)
                for c in candles
            ]

            query = """
                INSERT INTO candles (pair, granularity, timestamp, open, high, low, close, volume, complete)
                VALUES %s
                ON CONFLICT (pair, granularity, timestamp) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    complete = EXCLUDED.complete
            """

            with conn.cursor() as cur:
                execute_values(cur, query, rows)
            conn.commit()

            log.info(f"Guardadas {len(rows)} velas para {candles[0].pair} [{candles[0].granularity}]")
            return len(rows)
        except Exception as e:
            log.error(f"Error escribiendo velas en base de datos: {e}")
            if self._conn and not self._conn.closed:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
            self.close()
            raise

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None