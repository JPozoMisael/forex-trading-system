# shared/mt5_client.py
"""
Cliente para interactuar con MetaTrader 5.
Alternativa a OandaClient para usar MT5 como fuente de datos (solo pruebas locales).

IMPORTANTE: el paquete `MetaTrader5` solo funciona en Windows con el terminal
MT5 instalado y abierto en la misma máquina. No es apto para el despliegue en
Docker/Linux (Dokploy) de este proyecto: úsalo únicamente para correr el
data-collector de forma nativa en tu equipo mientras pruebas con la cuenta
demo, antes de decidirte por un broker con API REST para producción.
"""
from datetime import datetime
from typing import Optional, List, Dict, Any

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from shared.config import settings
from shared.logger import get_logger
from shared.models import Candle
from shared.market_client import MarketDataClient

log = get_logger(__name__)


class MT5Client(MarketDataClient):
    """Cliente para interactuar con MetaTrader 5"""

    def __init__(self):
        self.connected = False
        self.account_info = None

    def connect(self) -> bool:
        """Conectar a MT5"""
        if mt5 is None:
            log.error(
                "El paquete 'MetaTrader5' no está instalado o no es compatible con este sistema "
                "operativo (solo funciona en Windows). Instálalo con 'pip install MetaTrader5' "
                "en tu entorno local de pruebas."
            )
            return False

        if not mt5.initialize():
            log.error(f"Error inicializando MT5: {mt5.last_error()}")
            return False
        
        # Si hay credenciales configuradas, iniciar sesión
        if settings.mt5_login and settings.mt5_password:
            if not mt5.login(
                settings.mt5_login,
                password=settings.mt5_password,
                server=settings.mt5_server
            ):
                log.error(f"Error en login MT5: {mt5.last_error()}")
                mt5.shutdown()
                return False
        
        self.connected = True
        self.account_info = mt5.account_info()
        
        if self.account_info:
            log.info(f"✅ Conectado a MT5 - Cuenta: {self.account_info.login}")
            log.info(f"💰 Balance: {self.account_info.balance:.2f} {self.account_info.currency}")
            log.info(f"📈 Equity: {self.account_info.equity:.2f} {self.account_info.currency}")
        else:
            log.warning("⚠️ No se pudo obtener información de la cuenta")
        
        return True
    
    def disconnect(self):
        """Desconectar de MT5"""
        if self.connected:
            mt5.shutdown()
            self.connected = False
            log.info("🔌 Desconectado de MT5")
    
    def _convert_timeframe(self, granularity: str) -> int:
        """
        Convertir granularidad de string a constante MT5
        
        Args:
            granularity: "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"
        
        Returns:
            Constante de MT5 (ej: mt5.TIMEFRAME_M5)
        """
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
            "MN1": mt5.TIMEFRAME_MN1,
        }
        return mapping.get(granularity.upper(), mt5.TIMEFRAME_M5)
    
    def get_latest_candles(
        self, 
        pair: str, 
        granularity: str = "M5", 
        count: int = 5
    ) -> Optional[List[Candle]]:
        """
        Obtener las últimas velas de un par
        
        Args:
            pair: Par de divisas (ej: "EURUSD")
            granularity: "M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"
            count: Número de velas a obtener
            
        Returns:
            Lista de objetos Candle
        """
        if not self.connected:
            log.error("No conectado a MT5")
            return None
        
        # Convertir par de formato EUR_USD a EURUSD (MT5 usa sin guión bajo)
        symbol = pair.replace("_", "")
        
        timeframe = self._convert_timeframe(granularity)
        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
        
        if rates is None or len(rates) == 0:
            log.error(f"Error obteniendo velas de {symbol}")
            return None
        
        candles = []
        for rate in rates:
            candle = Candle(
                pair=pair,
                granularity=granularity,
                timestamp=datetime.fromtimestamp(rate[0]),
                open=rate[1],
                high=rate[2],
                low=rate[3],
                close=rate[4],
                volume=int(rate[5]),
                complete=True
            )
            candles.append(candle)
        
        log.info(f"📊 Obtenidas {len(candles)} velas de {pair}")
        return candles
    
    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        """Obtener precio actual de un par"""
        if not self.connected:
            log.error("No conectado a MT5")
            return None
        
        symbol = pair.replace("_", "")
        tick = mt5.symbol_info_tick(symbol)
        
        if tick:
            return {
                'bid': tick.bid,
                'ask': tick.ask,
                'spread': tick.ask - tick.bid,
                'timestamp': datetime.now()
            }
        
        log.error(f"No se pudo obtener precio de {pair}")
        return None
    
    def get_forex_pairs(self) -> List[str]:
        """Obtener lista de pares de divisas disponibles en formato EUR_USD"""
        if not self.connected:
            log.error("No conectado a MT5")
            return []
        
        symbols = mt5.symbols_get()
        forex_pairs = []
        for s in symbols:
            name = s.name
            # Filtrar pares de divisas principales
            if name.endswith(('USD', 'EUR', 'GBP', 'JPY', 'AUD', 'CHF', 'CAD')):
                # Convertir EURUSD a EUR_USD para mantener compatibilidad
                if len(name) == 6:
                    forex_pairs.append(f"{name[:3]}_{name[3:]}")
        
        return forex_pairs
    
    def get_account_info(self) -> Dict[str, Any]:
        """Obtener información de la cuenta"""
        if not self.connected:
            log.error("No conectado a MT5")
            return {}
        
        info = mt5.account_info()
        if info:
            return {
                'login': info.login,
                'balance': info.balance,
                'equity': info.equity,
                'margin': info.margin,
                'free_margin': info.margin_free,
                'leverage': info.leverage,
                'currency': info.currency,
                'server': info.server
            }
        return {}