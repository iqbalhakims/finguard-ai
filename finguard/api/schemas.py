"""Request / response contracts for the inference API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from finguard.config import CHANNELS, MERCHANT_CATEGORIES

MerchantCategory = Literal[tuple(MERCHANT_CATEGORIES)]  # type: ignore[valid-type]
Channel = Literal[tuple(CHANNELS)]  # type: ignore[valid-type]
Decision = Literal["approve", "review", "block"]

EXAMPLE = {
    "transaction_id": "txn_demo_0001",
    "customer_id": "cust_00042",
    "timestamp": "2026-11-14T02:13:00+08:00",
    "amount": 1249.99,
    "merchant_category": "electronics",
    "channel": "online",
    "card_present": False,
    "is_international": True,
    "new_device": True,
    "distance_from_home_km": 6800.0,
    "txn_count_1h": 3,
    "txn_count_24h": 4,
    "amount_sum_24h": 310.5,
    "avg_amount_30d": 58.2,
    "seconds_since_last_txn": 420,
}


class Transaction(BaseModel):
    """A card authorisation request plus the customer's behavioural aggregates.

    The aggregate fields (``txn_count_*``, ``amount_sum_24h``, ``avg_amount_30d``,
    ``seconds_since_last_txn``) are what a feature store would return for the
    customer at authorisation time; they are computed the same way as in
    ``finguard.data.process`` (strictly prior transactions only).
    """

    model_config = ConfigDict(json_schema_extra={"examples": [EXAMPLE]})

    transaction_id: str = Field(min_length=1, max_length=64)
    customer_id: str = Field(min_length=1, max_length=64)
    timestamp: datetime = Field(description="Authorisation time; the local wall-clock time is used")
    amount: float = Field(gt=0, le=1_000_000)
    merchant_category: MerchantCategory
    channel: Channel
    card_present: bool
    is_international: bool
    new_device: bool
    distance_from_home_km: float = Field(ge=0, le=25_000)

    txn_count_1h: int = Field(0, ge=0, description="Customer transactions in the previous hour")
    txn_count_24h: int = Field(0, ge=0, description="Customer transactions in the previous 24h")
    amount_sum_24h: float = Field(0.0, ge=0, description="Customer spend in the previous 24h")
    avg_amount_30d: float | None = Field(None, gt=0, description="Mean ticket over 30 days; null if no history")
    seconds_since_last_txn: float | None = Field(None, ge=0, description="Null for a first transaction")


class Contribution(BaseModel):
    feature: str
    value: float = Field(description="Model feature value (after feature engineering)")
    shap_value: float = Field(description="Contribution to the fraud log-odds")
    effect: Literal["increases_risk", "decreases_risk"]


class Explanation(BaseModel):
    method: str = "shap"
    base_value: float = Field(description="Expected model output (log-odds) before any feature")
    contributions: list[Contribution]


class Thresholds(BaseModel):
    review: float
    block: float


class Prediction(BaseModel):
    transaction_id: str
    fraud_probability: float
    decision: Decision
    is_fraud: bool = Field(description="True when decision is review or block")
    thresholds: Thresholds
    model_name: str
    model_version: str
    explanation: Explanation | None = None


class BatchRequest(BaseModel):
    transactions: list[Transaction] = Field(min_length=1, max_length=1000)


class BatchResponse(BaseModel):
    count: int
    flagged: int
    predictions: list[Prediction]
