
from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field, asdict
from enum import Enum


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    PAID = "PAID"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class Currency(str, Enum):
    OMR = "OMR"


_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.PENDING: frozenset({
        PaymentStatus.PROCESSING, PaymentStatus.FAILED, PaymentStatus.CANCELLED,
    }),
    PaymentStatus.PROCESSING: frozenset({
        PaymentStatus.PAID, PaymentStatus.FAILED, PaymentStatus.CANCELLED,
    }),
    PaymentStatus.PAID: frozenset({PaymentStatus.REFUNDED}),
    PaymentStatus.FAILED: frozenset(),
    PaymentStatus.CANCELLED: frozenset(),
    PaymentStatus.REFUNDED: frozenset(),
}


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    if current == target:
        return True
    return target in _TRANSITIONS[current]


class PaymentError(Exception):

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def new_reference() -> str:
    return f"JNP-{int(time.time())}-{secrets.token_hex(4).upper()}"


@dataclass
class PaymentAttempt:
    attempt_id: str
    provider: str
    status: PaymentStatus
    provider_reference: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class Payment:
    payment_id: str
    reference: str
    order_id: str
    customer_phone: str
    amount_baisa: int
    currency: Currency = Currency.OMR
    status: PaymentStatus = PaymentStatus.PENDING
    provider: str | None = None
    provider_reference: str | None = None
    idempotency_key: str | None = None
    attempts: list[PaymentAttempt] = field(default_factory=list)

    processed_events: list[str] = field(default_factory=list)
    refunded_baisa: int = 0
    failure_reason: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def amount_omr(self) -> float:
        return self.amount_baisa / 1000.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        d["currency"] = self.currency.value
        d["attempts"] = [a.to_dict() for a in self.attempts]
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Payment":
        attempts = [PaymentAttempt(**{**a, "status": PaymentStatus(a["status"])})
                    for a in d.get("attempts", [])]
        return cls(**{
            **d,
            "status": PaymentStatus(d["status"]),
            "currency": Currency(d.get("currency", "OMR")),
            "attempts": attempts,
        })

    def public(self) -> dict:
        return {
            "reference": self.reference,
            "orderID": self.order_id,
            "amount": self.amount_omr,
            "currency": self.currency.value,
            "status": self.status.value,
        }
