"""
Bus de mensajería unificado sobre Redis Streams.

A diferencia de Pub/Sub, los Streams persisten los mensajes: si un servicio
está caído o reiniciando, al volver procesa lo que quedó pendiente. Cada
servicio consume con su propio grupo de consumidores, así que varios servicios
pueden leer el mismo stream (p.ej. risk-manager y monitoring leen signals:new).

Además expone get_json/set_json para estado compartido (snapshot de cuenta,
especificaciones de símbolos).
"""
import json
import os
import socket
from datetime import datetime, date
from typing import Any, Callable, Generator, Optional

try:
    import redis
except ImportError:
    redis = None

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)

STREAM_MAXLEN = 10_000  # retención aproximada por stream


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        return super().default(obj)


def _serialize(message: Any) -> str:
    if hasattr(message, "model_dump_json"):
        return message.model_dump_json()
    if isinstance(message, str):
        return message
    return json.dumps(message, cls=DateTimeEncoder)


class EventBus:
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: Optional[int] = None,
    ):
        self.host = host or settings.redis_host
        self.port = port or settings.redis_port
        self.db = db if db is not None else settings.redis_db
        self._client = None

    def get_client(self):
        if redis is None:
            raise ImportError("El paquete 'redis' no está instalado en el entorno.")
        if self._client is None:
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                password=settings.redis_password or None,
                decode_responses=True,
                # Debe ser mayor que el BLOCK de XREADGROUP para no cortar lecturas.
                socket_timeout=15,
                socket_connect_timeout=5,
                retry_on_timeout=True,
            )
        return self._client

    # ------------------------------------------------------------------ streams
    def publish(self, channel: str, message: Any) -> str:
        """Agrega un mensaje (dict, modelo Pydantic o str) al stream `channel`."""
        payload = _serialize(message)
        try:
            msg_id = self.get_client().xadd(channel, {"data": payload}, maxlen=STREAM_MAXLEN, approximate=True)
            log.debug(f"Publicado en '{channel}' ({msg_id}): {payload[:120]}...")
            return msg_id
        except Exception as e:
            log.error(f"Error publicando en stream '{channel}': {e}")
            raise

    def _ensure_group(self, channel: str, group: str):
        try:
            # id="$": el grupo empieza a leer desde los mensajes nuevos.
            self.get_client().xgroup_create(channel, group, id="$", mkstream=True)
            log.info(f"Grupo de consumidores '{group}' creado en '{channel}'")
        except Exception as e:
            if "BUSYGROUP" not in str(e):
                raise

    def subscribe(
        self,
        *channels: str,
        group: str,
        consumer: Optional[str] = None,
        block_ms: int = 2000,
        should_stop: Callable[[], bool] = lambda: False,
        on_idle: Optional[Callable[[], None]] = None,
    ) -> Generator[tuple[str, dict], None, None]:
        """
        Consume uno o varios streams con un grupo de consumidores y produce
        tuplas (canal, data_dict).

        - Al arrancar entrega primero los mensajes que quedaron pendientes
          (leídos pero no confirmados) de una ejecución anterior.
        - Cada mensaje se confirma (XACK) cuando el consumidor pide el siguiente,
          es decir, después de haberlo procesado.
        - Cada `block_ms` sin mensajes revisa `should_stop` (apagado ordenado)
          y llama a `on_idle` (tareas periódicas en el mismo hilo).
        """
        client = self.get_client()
        consumer = consumer or f"{socket.gethostname()}-{os.getpid()}"
        for ch in channels:
            self._ensure_group(ch, group)
        log.info(f"Consumiendo streams {list(channels)} como grupo '{group}'")

        # "0" = pendientes propios primero; luego ">" = mensajes nuevos.
        cursor = {ch: "0" for ch in channels}

        while not should_stop():
            if on_idle:
                try:
                    on_idle()
                except Exception:
                    log.exception("Error en tarea periódica (on_idle)")

            try:
                response = client.xreadgroup(group, consumer, cursor, count=10, block=block_ms)
            except Exception as e:
                if redis is not None and isinstance(e, (redis.ConnectionError, redis.TimeoutError)):
                    log.warning(f"Redis no disponible ({e}); reintentando...")
                    self._sleep_interruptible(3, should_stop)
                    continue
                raise

            if not response:
                continue

            for channel, messages in response:
                if not messages and cursor[channel] == "0":
                    cursor[channel] = ">"  # ya no quedan pendientes
                    continue
                for msg_id, fields in messages:
                    raw = (fields or {}).get("data")
                    try:
                        data = json.loads(raw) if isinstance(raw, str) else {}
                    except json.JSONDecodeError:
                        log.warning(f"Mensaje no-JSON en '{channel}': {raw}")
                        data = {"raw": raw}
                    yield channel, data
                    client.xack(channel, group, msg_id)

    @staticmethod
    def _sleep_interruptible(seconds: int, should_stop: Callable[[], bool]):
        import time

        for _ in range(seconds):
            if should_stop():
                return
            time.sleep(1)

    # -------------------------------------------------------------- estado k/v
    def set_json(self, key: str, value: Any, ttl_seconds: Optional[int] = None):
        self.get_client().set(key, _serialize(value), ex=ttl_seconds)

    def set_json_if_absent(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> bool:
        return bool(self.get_client().set(key, _serialize(value), ex=ttl_seconds, nx=True))

    def get_json(self, key: str) -> Optional[Any]:
        raw = self.get_client().get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def ping(self) -> bool:
        if redis is None:
            return False
        try:
            return self.get_client().ping()
        except Exception:
            return False

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
