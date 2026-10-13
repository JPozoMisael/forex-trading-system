# shared/mt5_client.py
"""
Cliente MetaTrader 5: datos de mercado y ejecución para XM (o cualquier broker MT5).

IMPORTANTE: el paquete `MetaTrader5` solo funciona en Windows con el terminal
MT5 instalado, abierto y con "Algo Trading" habilitado en la misma máquina.
Por eso los servicios data-collector y execution-engine corren nativos en
Windows (ver scripts/start_local.ps1); Postgres y Redis pueden seguir en Docker.

Varios procesos pueden conectarse al mismo terminal a la vez.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from shared.config import settings
from shared.logger import get_logger
from shared.models import AccountSummary, Candle, OrderSide, SymbolSpec
from shared.market_client import MarketDataClient

log = get_logger(__name__)

# Retcodes de MT5 que vale la pena reintentar con un precio nuevo.
_RETRYABLE_RETCODES = {10004, 10020, 10021}  # REQUOTE, PRICE_CHANGED, PRICE_OFF
_RETCODE_DONE = 10009

# DEAL_REASON_* -> motivo de cierre legible
_DEAL_REASONS = {0: "manual", 1: "manual", 2: "manual", 3: "expert", 4: "sl", 5: "tp", 6: "stop_out"}


def _us_dst_active(dt_utc: datetime) -> bool:
    """Horario de verano de EE.UU.: 2º domingo de marzo 07:00 UTC -> 1er domingo de noviembre 06:00 UTC."""
    year = dt_utc.year
    march1 = datetime(year, 3, 1, tzinfo=timezone.utc)
    second_sunday_march = march1 + timedelta(days=(6 - march1.weekday()) % 7 + 7)
    nov1 = datetime(year, 11, 1, tzinfo=timezone.utc)
    first_sunday_nov = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
    return second_sunday_march + timedelta(hours=7) <= dt_utc < first_sunday_nov + timedelta(hours=6)


def server_utc_offset_seconds(at_utc: Optional[datetime] = None) -> int:
    """
    Desfase del reloj del servidor MT5 respecto a UTC.
    MT5 entrega los tiempos en hora del servidor. XM (como la mayoría) usa la
    convención "cierre de Nueva York": UTC+2 en invierno y UTC+3 en verano.
    """
    raw = settings.mt5_server_utc_offset.strip().lower()
    if raw and raw != "auto":
        return int(float(raw) * 3600)
    at_utc = at_utc or datetime.now(timezone.utc)
    return (3 if _us_dst_active(at_utc) else 2) * 3600


def server_epoch_to_utc(epoch: int) -> datetime:
    """Convierte un timestamp de MT5 (hora del servidor) a datetime UTC real."""
    approx = datetime.fromtimestamp(epoch, tz=timezone.utc)
    return approx - timedelta(seconds=server_utc_offset_seconds(approx))


class MT5Client(MarketDataClient):
    """Cliente para interactuar con MetaTrader 5"""

    def __init__(self):
        self.connected = False
        self.account_info = None
        self._symbol_cache: Dict[str, str] = {}

    # ------------------------------------------------------------ conexión
    def connect(self) -> bool:
        if mt5 is None:
            log.error(
                "El paquete 'MetaTrader5' no está instalado o no es compatible con este sistema "
                "operativo (solo funciona en Windows). Instálalo con 'pip install MetaTrader5'."
            )
            return False

        kwargs: Dict[str, Any] = {"timeout": settings.mt5_timeout}
        if settings.mt5_path:
            kwargs["path"] = settings.mt5_path
        if settings.mt5_login and settings.mt5_password:
            kwargs.update(login=settings.mt5_login, password=settings.mt5_password)
            if settings.mt5_server:
                kwargs["server"] = settings.mt5_server

        if not mt5.initialize(**kwargs):
            log.error(f"Error inicializando MT5: {mt5.last_error()}. ¿Está el terminal abierto?")
            return False

        self.account_info = mt5.account_info()
        if self.account_info is None:
            log.error(f"MT5 inicializado pero sin cuenta: {mt5.last_error()}")
            mt5.shutdown()
            return False

        self.connected = True
        kind = "REAL" if self.is_real_account else "DEMO"
        log.info(f"✅ Conectado a MT5 [{kind}] - Cuenta {self.account_info.login} @ {self.account_info.server}")
        log.info(f"💰 Balance: {self.account_info.balance:.2f} {self.account_info.currency} | Equity: {self.account_info.equity:.2f}")
        return True

    def ensure_connected(self) -> bool:
        """Reconecta si el terminal se cerró o perdió la sesión."""
        if self.connected and mt5 is not None and mt5.terminal_info() is not None and mt5.account_info() is not None:
            return True
        log.warning("Conexión MT5 perdida; reintentando...")
        self.connected = False
        if mt5 is not None:
            mt5.shutdown()
        return self.connect()

    @property
    def is_real_account(self) -> bool:
        # trade_mode: 0 = demo, 1 = concurso, 2 = real
        return bool(self.account_info and self.account_info.trade_mode == 2)

    def terminal_trade_allowed(self) -> bool:
        info = mt5.terminal_info() if mt5 else None
        return bool(info and info.trade_allowed)

    def disconnect(self):
        if self.connected:
            mt5.shutdown()
            self.connected = False
            log.info("🔌 Desconectado de MT5")

    # ------------------------------------------------------------ símbolos
    def symbol_for(self, pair: str) -> str:
        """EUR_USD -> nombre del símbolo en el broker (con sufijo, p.ej. EURUSDmicro)."""
        if pair in self._symbol_cache:
            return self._symbol_cache[pair]
        symbol = pair.replace("_", "") + settings.mt5_symbol_suffix
        if mt5 is not None and self.connected:
            if mt5.symbol_info(symbol) is None:
                raise ValueError(
                    f"El símbolo '{symbol}' no existe en {settings.mt5_server or 'el servidor'}. "
                    f"Revisa MT5_SYMBOL_SUFFIX (usa scripts/check_mt5.py para ver los nombres)."
                )
            mt5.symbol_select(symbol, True)  # asegura que esté en Market Watch
        self._symbol_cache[pair] = symbol
        return symbol

    def _convert_timeframe(self, granularity: str) -> int:
        mapping = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D": mt5.TIMEFRAME_D1,
            "D1": mt5.TIMEFRAME_D1,
            "W1": mt5.TIMEFRAME_W1,
            "MN1": mt5.TIMEFRAME_MN1,
        }
        tf = mapping.get(granularity.upper())
        if tf is None:
            raise ValueError(f"Granularidad no soportada por MT5: {granularity}")
        return tf

    # ------------------------------------------------------------ datos
    def get_latest_candles(self, pair: str, granularity: str = "M5", count: int = 5) -> List[Candle]:
        """
        Últimas `count` velas CERRADAS. Se empieza en la posición 1 porque la
        posición 0 es la vela en formación, que todavía cambia (repintado).
        Los timestamps se convierten de hora del servidor a UTC real.
        """
        if not self.ensure_connected():
            return []

        symbol = self.symbol_for(pair)
        rates = mt5.copy_rates_from_pos(symbol, self._convert_timeframe(granularity), 1, count)
        if rates is None or len(rates) == 0:
            log.error(f"Error obteniendo velas de {symbol}: {mt5.last_error()}")
            return []

        return [
            Candle(
                pair=pair,
                granularity=granularity,
                timestamp=server_epoch_to_utc(int(r["time"])),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=int(r["tick_volume"]),
                complete=True,
            )
            for r in rates
        ]

    def get_price(self, pair: str) -> Optional[Dict[str, Any]]:
        if not self.ensure_connected():
            return None
        tick = mt5.symbol_info_tick(self.symbol_for(pair))
        if tick is None or tick.bid <= 0:
            log.error(f"No se pudo obtener precio de {pair}")
            return None
        return {
            "bid": tick.bid,
            "ask": tick.ask,
            "spread": tick.ask - tick.bid,
            "timestamp": server_epoch_to_utc(int(tick.time)),
        }

    def get_forex_pairs(self) -> List[str]:
        """Pares de divisas disponibles en formato EUR_USD (sin sufijo)."""
        if not self.ensure_connected():
            return []
        suffix = settings.mt5_symbol_suffix
        pairs = []
        for s in mt5.symbols_get() or []:
            name = s.name[: -len(suffix)] if suffix and s.name.endswith(suffix) else s.name
            if len(name) == 6 and name.isalpha() and (not suffix or s.name.endswith(suffix)):
                pairs.append(f"{name[:3]}_{name[3:]}")
        return pairs

    # ------------------------------------------------------------ cuenta
    def get_account_summary(self) -> Optional[AccountSummary]:
        if not self.ensure_connected():
            return None
        info = mt5.account_info()
        if info is None:
            return None
        self.account_info = info
        positions = mt5.positions_get() or []
        return AccountSummary(
            balance=info.balance,
            equity=info.equity,
            unrealized_pnl=info.profit,
            margin_used=info.margin,
            margin_available=info.margin_free,
            open_positions_count=len(positions),
            currency=info.currency,
            source="mt5",
            login=info.login,
            server=info.server,
            is_real_account=info.trade_mode == 2,
        )

    def get_account_info(self) -> Dict[str, Any]:
        summary = self.get_account_summary()
        return summary.model_dump() if summary else {}

    def get_symbol_spec(self, pair: str) -> Optional[SymbolSpec]:
        if not self.ensure_connected():
            return None
        symbol = self.symbol_for(pair)
        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return SymbolSpec(
            pair=pair,
            symbol=symbol,
            digits=info.digits,
            point=info.point,
            tick_size=info.trade_tick_size,
            tick_value=info.trade_tick_value,
            contract_size=info.trade_contract_size,
            volume_min=info.volume_min,
            volume_max=info.volume_max,
            volume_step=info.volume_step,
            stops_level_points=info.trade_stops_level,
            spread_points=info.spread,
            currency_profit=info.currency_profit,
        )

    # ------------------------------------------------------------ trading
    def _filling_mode(self, info) -> int:
        # symbol_info.filling_mode es una máscara: 1 = FOK, 2 = IOC
        if info.filling_mode & 1:
            return mt5.ORDER_FILLING_FOK
        if info.filling_mode & 2:
            return mt5.ORDER_FILLING_IOC
        return mt5.ORDER_FILLING_RETURN

    def market_order(
        self,
        pair: str,
        side: OrderSide,
        volume_lots: float,
        sl_distance: float,
        tp_distance: float,
        comment: str,
    ) -> Dict[str, Any]:
        """
        Abre una posición a mercado con SL y TP adjuntos.
        SL/TP se pasan como DISTANCIAS y se recalculan sobre el precio real de
        ejecución, así el riesgo en dinero es el que calculó el risk-manager
        aunque el precio se haya movido desde la señal.

        Retorna {"ok": bool, "ticket", "price", "sl", "tp", "volume", "error"}.
        """
        if not self.ensure_connected():
            return {"ok": False, "error": "MT5 no conectado"}
        if not self.terminal_trade_allowed():
            return {"ok": False, "error": "Algo Trading deshabilitado en el terminal MT5 (botón 'Algo Trading')"}

        symbol = self.symbol_for(pair)
        info = mt5.symbol_info(symbol)
        if info is None or not info.visible:
            return {"ok": False, "error": f"Símbolo {symbol} no disponible"}

        min_stop = info.trade_stops_level * info.point
        if sl_distance <= min_stop or tp_distance <= min_stop:
            return {"ok": False, "error": f"SL/TP más cerca que el mínimo del broker ({info.trade_stops_level} points)"}

        last_error = "sin respuesta"
        for attempt in range(3):
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                return {"ok": False, "error": "Sin cotización (¿mercado cerrado?)"}

            if side == OrderSide.BUY:
                price, order_type = tick.ask, mt5.ORDER_TYPE_BUY
                sl, tp = price - sl_distance, price + tp_distance
            else:
                price, order_type = tick.bid, mt5.ORDER_TYPE_SELL
                sl, tp = price + sl_distance, price - tp_distance

            request = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": float(volume_lots),
                "type": order_type,
                "price": price,
                "sl": round(sl, info.digits),
                "tp": round(tp, info.digits),
                "deviation": settings.slippage,
                "magic": settings.mt5_magic,
                "comment": comment[:31],
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._filling_mode(info),
            }

            result = mt5.order_send(request)
            if result is None:
                last_error = f"order_send devolvió None: {mt5.last_error()}"
                break
            if result.retcode == _RETCODE_DONE:
                return {
                    "ok": True,
                    # En MT5 el id de la posición es el ticket de la orden que la abrió.
                    "ticket": str(result.order),
                    "price": result.price or price,
                    "sl": request["sl"],
                    "tp": request["tp"],
                    "volume": result.volume,
                }
            last_error = f"retcode={result.retcode} {result.comment}"
            if result.retcode not in _RETRYABLE_RETCODES:
                break
            log.warning(f"Recotización en {symbol} ({last_error}); reintento {attempt + 1}/3")
            time.sleep(0.5)

        return {"ok": False, "error": last_error}

    def get_open_positions(self) -> Dict[str, Any]:
        """Posiciones abiertas por este sistema (filtradas por magic number), indexadas por ticket."""
        if not self.ensure_connected():
            raise ConnectionError("MT5 no conectado")
        positions = mt5.positions_get()
        if positions is None:
            raise ConnectionError(f"positions_get falló: {mt5.last_error()}")
        return {str(p.ticket): p for p in positions if p.magic == settings.mt5_magic}

    def get_closed_position(self, ticket: str) -> Optional[Dict[str, Any]]:
        """
        Resultado de una posición ya cerrada a partir del historial de deals.
        El PnL incluye comisión, swap y fees, en la divisa de la cuenta.
        """
        if not self.ensure_connected():
            return None
        deals = mt5.history_deals_get(position=int(ticket))
        if not deals:
            return None
        exits = [d for d in deals if d.entry in (mt5.DEAL_ENTRY_OUT, mt5.DEAL_ENTRY_OUT_BY)]
        if not exits:
            return None
        last = exits[-1]
        pnl = sum(d.profit + d.commission + d.swap + getattr(d, "fee", 0.0) for d in deals)
        return {
            "exit_price": last.price,
            "pnl": round(pnl, 2),
            "exit_reason": _DEAL_REASONS.get(last.reason, "manual"),
            "closed_at": server_epoch_to_utc(int(last.time)),
        }

    def find_position_by_comment(self, comment: str) -> Optional[Any]:
        if not self.ensure_connected():
            return None
        for p in mt5.positions_get() or []:
            if p.magic == settings.mt5_magic and p.comment == comment:
                return p
        return None
