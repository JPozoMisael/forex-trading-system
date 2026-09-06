"""
Wrapper sobre oandapyV20 para obtener velas OHLC y datos de precios de OANDA.
"""
from datetime import datetime, timezone
from typing import List, Optional
import math
import random

from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.pricing import PricingInfo
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings
from shared.logger import get_logger
from shared.models import Candle

log = get_logger(__name__)


class OandaClient:
    def __init__(self, api_key: Optional[str] = None, environment: Optional[str] = None):
        self.api_key = api_key or settings.oanda_api_key
        self.environment = environment or settings.oanda_environment
        self._api: Optional[API] = None

        if self.api_key and self.api_key != "tu_api_key_aqui":
            try:
                self._api = API(
                    access_token=self.api_key,
                    environment=self.environment,
                )
            except Exception as e:
                log.error(f"Error inicializando OANDA API: {e}")
                self._api = None
        else:
            log.warning("OANDA_API_KEY no configurada o es plantilla. Funcionando en modo Simulación/Dry-Run.")

    @property
    def is_live_connected(self) -> bool:
        return self._api is not None

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def get_latest_candles(self, pair: str, granularity: str = "M5", count: int = 10) -> List[Candle]:
        """
        Obtiene las últimas `count` velas completas de un par.
        """
        if not self._api:
            return self._generate_simulated_candles(pair=pair, granularity=granularity, count=count)

        params = {"count": count, "granularity": granularity, "price": "M"}  # M = mid price
        request = InstrumentsCandles(instrument=pair, params=params)

        try:
            response = self._api.request(request)
        except Exception:
            log.exception(f"Error en request OANDA para {pair}")
            raise

        candles = []
        for raw in response.get("candles", []):
            if not raw.get("complete", True):
                continue  # ignoramos la vela en formación para evitar datos incompletos
            mid = raw["mid"]
            candles.append(
                Candle(
                    pair=pair,
                    granularity=granularity,
                    timestamp=datetime.fromisoformat(raw["time"].replace("Z", "+00:00")),
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=int(raw.get("volume", 0)),
                    complete=True,
                )
            )
        return candles

    def _generate_simulated_candles(self, pair: str, granularity: str, count: int) -> List[Candle]:
        """
        Genera datos realistas en modo simulación cuando no se dispone de API Key.
        """
        base_prices = {
            "EUR_USD": 1.0850,
            "USD_JPY": 155.20,
            "GBP_USD": 1.2950,
            "AUD_USD": 0.6550,
            "USD_CHF": 0.8950,
        }
        base_price = base_prices.get(pair, 1.0000)
        now = datetime.now(timezone.utc)
        step_seconds = 300 if granularity == "M5" else (60 if granularity == "M1" else 3600)

        candles = []
        current_price = base_price
        for i in range(count, 0, -1):
            ts = datetime.fromtimestamp(now.timestamp() - (i * step_seconds), tz=timezone.utc)
            delta = random.gauss(0, base_price * 0.0005)
            open_p = round(current_price, 5)
            close_p = round(open_p + delta, 5)
            high_p = round(max(open_p, close_p) + abs(random.gauss(0, base_price * 0.0003)), 5)
            low_p = round(min(open_p, close_p) - abs(random.gauss(0, base_price * 0.0003)), 5)
            vol = random.randint(50, 500)
            current_price = close_p

            candles.append(
                Candle(
                    pair=pair,
                    granularity=granularity,
                    timestamp=ts,
                    open=open_p,
                    high=high_p,
                    low=low_p,
                    close=close_p,
                    volume=vol,
                    complete=True,
                )
            )
        return candles