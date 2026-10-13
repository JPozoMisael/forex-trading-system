"""
Esquemas de datos compartidos entre todos los microservicios del sistema.
Utiliza Pydantic v2 para validación, serialización y tipado estricto.
"""
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    FLAT = "flat"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"        # reservada por el risk-manager, aún no enviada
    SUBMITTING = "submitting"  # el execution-engine la está enviando al broker
    APPROVED = "approved"
    REJECTED = "rejected"
    FILLED = "filled"          # posición abierta en el broker
    CANCELLED = "cancelled"
    CLOSED = "closed"


# Estados que cuentan como posición activa (para límites de riesgo).
ACTIVE_ORDER_STATUSES = ("pending", "submitting", "filled")


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class Candle(BaseModel):
    pair: str
    granularity: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0
    complete: bool = True

    model_config = {"extra": "ignore"}


class Signal(BaseModel):
    pair: str
    strategy_name: str
    direction: SignalDirection
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class RiskAssessment(BaseModel):
    approved: bool
    reason: str = "OK"
    recommended_units: int = 0
    recommended_lots: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_amount_usd: float = 0.0
    risk_percent: float = 0.0
    account_balance: float = 0.0

    model_config = {"extra": "ignore"}


class SymbolSpec(BaseModel):
    """Especificación de un símbolo en el broker (equivalente a mt5.symbol_info)."""
    pair: str                 # formato interno EUR_USD
    symbol: str               # nombre en el broker, p.ej. EURUSD o EURUSDmicro
    digits: int
    point: float
    tick_size: float
    tick_value: float         # valor de 1 tick para 1 lote, en la divisa de la cuenta
    contract_size: float      # unidades por lote
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int = 0
    spread_points: int = 0
    currency_profit: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"extra": "ignore"}


class OrderRequest(BaseModel):
    order_id: Optional[int] = None  # fila reservada en la tabla orders
    pair: str
    direction: OrderSide
    units: int = Field(gt=0)
    volume_lots: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    strategy_name: str = "manual"
    signal_timestamp: Optional[datetime] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class OrderExecution(BaseModel):
    order_id: Optional[int] = None
    pair: str
    direction: OrderSide
    units: int
    entry_price: float
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    volume_lots: Optional[float] = None
    status: OrderStatus = OrderStatus.FILLED
    broker_ticket: Optional[str] = None
    error: Optional[str] = None
    opened_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    strategy_name: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


class AccountSummary(BaseModel):
    balance: float
    equity: float
    day_start_equity: Optional[float] = None  # base del circuit breaker diario
    source: str = "simulated"                 # "mt5" | "simulated"
    login: Optional[int] = None
    server: Optional[str] = None
    is_real_account: bool = False
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin_used: float = 0.0
    margin_available: float = 0.0
    open_positions_count: int = 0
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"extra": "ignore"}


class TradeResult(BaseModel):
    order_id: Optional[int] = None
    pair: str
    direction: OrderSide
    units: int
    entry_price: float
    exit_price: float
    pnl: Optional[float]  # en la divisa de la cuenta; None si no se pudo convertir
    pips: float
    roi_pct: float
    opened_at: datetime
    closed_at: datetime
    duration_minutes: float
    strategy_name: str
    exit_reason: str = "normal"  # "tp" | "sl" | "manual" | "stop_out" | "end_of_data"

    model_config = {"extra": "ignore"}