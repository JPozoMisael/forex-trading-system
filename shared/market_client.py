"""
Contrato común para cualquier fuente de datos de mercado (OANDA, MT5, futuros brokers).

Cada broker soportado implementa esta interfaz en su propio módulo
(services/data-collector/src/oanda_client.py, shared/mt5_client.py, etc.).
El resto del pipeline (db_writer, EventBus, estrategias) solo conoce este
contrato, nunca la librería específica de un broker, así que agregar o
cambiar de broker no obliga a tocar el resto del sistema.
"""
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from shared.models import Candle


class MarketDataClient(ABC):
    """Interfaz mínima que debe cumplir cualquier cliente de datos de mercado."""

    @abstractmethod
    def connect(self) -> bool:
        """Establece la conexión con el broker. Retorna True si fue exitosa."""
        raise NotImplementedError

    @abstractmethod
    def get_latest_candles(self, pair: str, granularity: str = "M5", count: int = 10) -> List[Candle]:
        """Retorna las últimas `count` velas completas de un par."""
        raise NotImplementedError

    @abstractmethod
    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        """Retorna el precio bid/ask actual de un par, o None si no está disponible."""
        raise NotImplementedError
