"""Order request/response schemas shared by every broker implementation."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class OrderRequest(BaseModel):
    strategy_id: str
    mode: str  # "paper" | "live"
    market: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    limit_price: float | None = None
    stop_price: float | None = None


class OrderResult(BaseModel):
    order_id: str
    status: OrderStatus
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    rejection_reason: str | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
