"""
Wrapper sobre oandapyV20 para obtener velas OHLC y datos de precios de OANDA.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import math
import random

from oandapyV20 import API
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.pricing import PricingInfo
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings
from shared.logger import get_logger
from shared.models import Candle
from shared.market_client import MarketDataClient

log = get_logger(__name__)

# Precios base usados para generar datos simulados (modo Dry-Run sin API key real).
# Única fuente para _generate_simulated_price y _generate_simulated_candles: evita
# que ambos generadores queden desincronizados si se agrega o cambia un par.
_SIMULATED_BASE_PRICES = {
    "EUR_USD": 1.0850,
    "USD_JPY": 155.20,
    "GBP_USD": 1.2950,
    "AUD_USD": 0.6550,
    "USD_CHF": 0.8950,
}


class OandaClient(MarketDataClient):
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

    def connect(self) -> bool:
        """OANDA se autentica en el constructor; este método solo confirma el estado."""
        if not self.is_live_connected:
            log.info("OANDA operando en modo Simulación/Dry-Run (sin API key real).")
        return True

    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        """Obtiene el precio bid/ask actual de un par vía la API de pricing de OANDA."""
        if not self._api:
            return self._generate_simulated_price(pair)

        try:
            request = PricingInfo(accountID=settings.oanda_account_id, params={"instruments": pair})
            response = self._api.request(request)
            prices = response.get("prices", [])
            if not prices:
                return None
            quote = prices[0]
            bid = float(quote["bids"][0]["price"])
            ask = float(quote["asks"][0]["price"])
            return {"bid": bid, "ask": ask, "spread": ask - bid, "timestamp": datetime.now(timezone.utc)}
        except Exception:
            log.exception(f"Error obteniendo precio OANDA para {pair}")
            return None

    def _generate_simulated_price(self, pair: str) -> Dict[str, Any]:
        """Genera un precio simulado cuando no hay API key real configurada."""
        mid = _SIMULATED_BASE_PRICES.get(pair, 1.0000)
        spread = mid * 0.00015
        return {
            "bid": round(mid - spread / 2, 5),
            "ask": round(mid + spread / 2, 5),
            "spread": round(spread, 5),
            "timestamp": datetime.now(timezone.utc),
        }

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
        base_price = _SIMULATED_BASE_PRICES.get(pair, 1.0000)
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