"""
Cliente de datos de mercado vía la API REST v20 de OANDA.

Corre en cualquier sistema (Linux/Docker incluido). Una cuenta demo
("practice") gratuita basta para obtener precios en tiempo real e historial.

Sin credenciales válidas el servicio NO arranca: antes generaba velas
aleatorias en silencio y las estrategias terminaban analizando ruido.
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from oandapyV20 import API
from oandapyV20.endpoints.accounts import AccountList, AccountSummary
from oandapyV20.endpoints.instruments import InstrumentsCandles
from oandapyV20.endpoints.pricing import PricingInfo
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import settings
from shared.logger import get_logger
from shared.models import Candle
from shared.market_client import MarketDataClient

log = get_logger(__name__)

# OANDA admite hasta 5000 velas por petición.
MAX_CANDLES_PER_REQUEST = 5000

_GRANULARITY_ALIASES = {"D1": "D", "W1": "W", "MN1": "M"}


def _parse_oanda_time(raw: str) -> datetime:
    """'2026-10-13T12:05:00.000000000Z' -> datetime UTC (recorta nanosegundos a microsegundos)."""
    raw = re.sub(r"(\.\d{6})\d+", r"\1", raw.replace("Z", "+00:00"))
    return datetime.fromisoformat(raw).astimezone(timezone.utc)


class OandaClient(MarketDataClient):
    def __init__(self, api_key: Optional[str] = None, account_id: Optional[str] = None, environment: Optional[str] = None):
        # strip de espacios y comillas: es común pegarlas por error en los paneles de variables
        self.api_key = (api_key or settings.oanda_api_key).strip().strip("'\"")
        self.account_id = (account_id or settings.oanda_account_id).strip().strip("'\"")
        self.environment = (environment or settings.oanda_environment).strip().lower()
        self._api: Optional[API] = None

    def connect(self) -> bool:
        """Valida las credenciales haciendo una consulta real a la cuenta."""
        if not self.api_key or not self.account_id:
            log.error("Faltan OANDA_API_KEY u OANDA_ACCOUNT_ID en las variables de entorno.")
            return False
        if self.environment not in ("practice", "live"):
            log.error(f"OANDA_ENVIRONMENT='{self.environment}' inválido: usa 'practice' (demo) o 'live'.")
            return False

        try:
            self._api = API(access_token=self.api_key, environment=self.environment)
            summary = self._api.request(AccountSummary(accountID=self.account_id))["account"]
        except Exception as e:
            log.error(
                f"OANDA rechazó la conexión ({e}). Revisa que el token y el número de cuenta sean "
                f"correctos y que OANDA_ENVIRONMENT='{self.environment}' coincida con el tipo de cuenta."
            )
            self._log_available_accounts()
            self._api = None
            return False

        log.info(
            f"✅ Conectado a OANDA [{self.environment}] - Cuenta {self.account_id} | "
            f"Balance {summary.get('balance')} {summary.get('currency')}"
        )
        return True

    def _log_available_accounts(self):
        """Si el token es válido, muestra los números de cuenta que puede usar (ayuda a configurar OANDA_ACCOUNT_ID)."""
        try:
            accounts = self._api.request(AccountList()).get("accounts", [])
        except Exception:
            return  # el token tampoco es válido para este entorno
        ids = [a.get("id") for a in accounts if a.get("id")]
        if ids:
            log.error(f"👉 Tu token es válido. Cuentas disponibles para OANDA_ACCOUNT_ID: {', '.join(ids)}")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10), reraise=True)
    def get_latest_candles(self, pair: str, granularity: str = "M5", count: int = 10) -> List[Candle]:
        """
        Últimas velas CERRADAS de un par (la vela en formación se descarta).
        Se pide una vela de más para compensar la que se descarta.
        """
        if self._api is None:
            raise ConnectionError("Cliente OANDA no conectado; llama a connect() primero.")

        gran = _GRANULARITY_ALIASES.get(granularity.upper(), granularity.upper())
        params = {"count": min(count + 1, MAX_CANDLES_PER_REQUEST), "granularity": gran, "price": "M"}
        response = self._api.request(InstrumentsCandles(instrument=pair, params=params))

        candles = []
        for raw in response.get("candles", []):
            if not raw.get("complete", False):
                continue  # vela en formación: todavía cambia
            mid = raw["mid"]
            candles.append(
                Candle(
                    pair=pair,
                    granularity=granularity,
                    timestamp=_parse_oanda_time(raw["time"]),
                    open=float(mid["o"]),
                    high=float(mid["h"]),
                    low=float(mid["l"]),
                    close=float(mid["c"]),
                    volume=int(raw.get("volume", 0)),
                    complete=True,
                )
            )
        return candles[-count:]

    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        """Precio bid/ask actual de un par."""
        if self._api is None:
            return None
        try:
            response = self._api.request(PricingInfo(accountID=self.account_id, params={"instruments": pair}))
            prices = response.get("prices", [])
            if not prices:
                return None
            quote = prices[0]
            bid = float(quote["bids"][0]["price"])
            ask = float(quote["asks"][0]["price"])
            return {
                "bid": bid,
                "ask": ask,
                "spread": ask - bid,
                "tradeable": quote.get("tradeable", True),
                "timestamp": _parse_oanda_time(quote["time"]),
            }
        except Exception:
            log.exception(f"Error obteniendo precio OANDA para {pair}")
            return None
