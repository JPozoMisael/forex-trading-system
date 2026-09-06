"""
Publicador y persistidor de señales generadas por las estrategias.
Inserta en la tabla 'signals' de TimescaleDB y emite al bus de eventos Redis.
"""
import json
from typing import Optional
import psycopg2
from psycopg2.extras import Json

from shared.config import settings
from shared.logger import get_logger
from shared.models import Signal
from shared.bus import EventBus

log = get_logger(__name__)


class SignalPublisher:
    def __init__(self, dsn: Optional[str] = None, bus: Optional[EventBus] = None):
        self.dsn = dsn or settings.postgres_dsn
        self.bus = bus or EventBus()
        self._conn: Optional[psycopg2.extensions.connection] = None

    def _get_connection(self) -> psycopg2.extensions.connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = True
        return self._conn

    def publish(self, signal: Signal) -> bool:
        """
        Guarda la señal en la base de datos y la publica al canal 'signals:new'.
        """
        # 1. Guardar en Base de Datos
        try:
            conn = self._get_connection()
            query = """
                INSERT INTO signals (pair, strategy_name, direction, confidence, timestamp, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id;
            """
            meta = signal.metadata.copy()
            if signal.entry_price is not None:
                meta["entry_price"] = signal.entry_price
            if signal.stop_loss is not None:
                meta["stop_loss"] = signal.stop_loss
            if signal.take_profit is not None:
                meta["take_profit"] = signal.take_profit

            with conn.cursor() as cur:
                cur.execute(
                    query,
                    (
                        signal.pair,
                        signal.strategy_name,
                        signal.direction.value,
                        signal.confidence,
                        signal.timestamp,
                        Json(meta),
                    ),
                )
                signal_id = cur.fetchone()[0]
                meta["db_signal_id"] = signal_id
                signal.metadata = meta

            log.info(f"Señal guardada en BD con ID={signal_id}: {signal.pair} {signal.direction.value} ({signal.strategy_name})")
        except Exception as db_err:
            log.warning(f"No se pudo persistir señal en BD (continuando con bus): {db_err}")

        # 2. Publicar a Redis
        try:
            self.bus.publish(settings.channel_signals_new, signal)
            log.info(f"📡 Señal publicada a '{settings.channel_signals_new}': {signal.pair} {signal.direction.value.upper()} @ {signal.entry_price}")
            return True
        except Exception as bus_err:
            log.error(f"Error publicando señal al bus Redis: {bus_err}")
            return False

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None
        self.bus.close()
