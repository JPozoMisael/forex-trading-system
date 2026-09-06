"""
Bus de mensajería unificado sobre Redis.
Proporciona métodos publish y subscribe con serialización/deserialización JSON automática.
Permite una comunicación desacoplada y en tiempo real entre microservicios.
"""
import json
from datetime import datetime, date
from typing import Any, Generator, Optional

try:
    import redis
except ImportError:
    redis = None

from shared.config import settings
from shared.logger import get_logger

log = get_logger(__name__)


class DateTimeEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if hasattr(obj, "dict"):
            return obj.dict()
        return super().default(obj)


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
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True,
            )
        return self._client

    def publish(self, channel: str, message: Any) -> int:
        """
        Publica un mensaje (dict, objeto Pydantic o primitivo) en un canal de Redis.
        """
        try:
            client = self.get_client()
            if hasattr(message, "model_dump_json"):
                payload = message.model_dump_json()
            elif isinstance(message, (dict, list)):
                payload = json.dumps(message, cls=DateTimeEncoder)
            elif isinstance(message, str):
                payload = message
            else:
                payload = json.dumps(message, cls=DateTimeEncoder)

            subscribers = client.publish(channel, payload)
            log.debug(f"Publicado en '{channel}' ({subscribers} suscriptores): {payload[:120]}...")
            return subscribers
        except Exception as e:
            log.error(f"Error publicando en canal '{channel}': {e}")
            raise

    def subscribe(self, *channels: str) -> Generator[tuple[str, dict], None, None]:
        """
        Generador que se suscribe a uno o varios canales y produce tuplas (canal, data_dict).
        Maneja reconexión y parseo JSON seguro.
        """
        client = self.get_client()
        pubsub = client.pubsub()
        pubsub.subscribe(*channels)
        log.info(f"Suscrito a canales: {list(channels)}")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"]
            raw_data = message["data"]
            try:
                data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
                yield channel, data
            except json.JSONDecodeError:
                log.warning(f"Mensaje no-JSON recibido en '{channel}': {raw_data}")
                yield channel, {"raw": raw_data}
            except Exception as e:
                log.exception(f"Error procesando mensaje de '{channel}': {e}")

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
