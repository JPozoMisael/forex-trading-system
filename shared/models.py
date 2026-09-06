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
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    FILLED = "filled"
    CANCELLED = "cancelled"
    CLOSED = "closed"


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
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_amount_usd: float = 0.0
    risk_percent: float = 0.0
    account_balance: float = 0.0

    model_config = {"extra": "ignore"}


class OrderRequest(BaseModel):
    pair: str
    direction: OrderSide
    units: int = Field(gt=0)
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
    status: OrderStatus = OrderStatus.FILLED
    oanda_order_id: Optional[str] = None
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
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    margin_used: float = 0.0
    margin_available: float = 0.0
    open_positions_count: int = 0
    currency: str = "USD"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"extra": "ignore"}


class TradeResult(BaseModel):
    pair: str
    direction: OrderSide
    units: int
    entry_price: float
    exit_price: float
    pnl: float
    pips: float
    roi_pct: float
    opened_at: datetime
    closed_at: datetime
    duration_minutes: float
    strategy_name: str
    exit_reason: str = "normal"  # "tp" | "sl" | "signal" | "reconciliation"

    model_config = {"extra": "ignore"}