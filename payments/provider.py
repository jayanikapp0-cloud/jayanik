
from __future__ import annotations

from dataclasses import dataclass, field

from .models import PaymentStatus


@dataclass
class ProviderResult:
    status: PaymentStatus
    provider_reference: str | None = None
    redirect_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class WebhookEvent:
    event_id: str
    reference: str
    status: PaymentStatus
    provider_reference: str | None = None
    amount_baisa: int | None = None
    raw: dict = field(default_factory=dict)


class PaymentProvider:


    name: str = "unset"


    supports_capture: bool = False


    supports_partial_refund: bool = False

    def create_payment(self, *, reference: str, amount_baisa: int, currency: str,
                       customer_phone: str, order_id: str,
                       return_url: str | None = None) -> ProviderResult:
        raise NotImplementedError

    def initialize_payment(self, *, reference: str,
                           provider_reference: str) -> ProviderResult:
        raise NotImplementedError

    def verify_payment(self, *, reference: str,
                       provider_reference: str) -> ProviderResult:
        raise NotImplementedError

    def capture_payment(self, *, reference: str, provider_reference: str,
                        amount_baisa: int | None = None) -> ProviderResult:
        raise NotImplementedError

    def cancel_payment(self, *, reference: str,
                       provider_reference: str) -> ProviderResult:
        raise NotImplementedError

    def refund_payment(self, *, reference: str, provider_reference: str,
                       amount_baisa: int, reason: str = "") -> ProviderResult:
        raise NotImplementedError

    def get_payment_status(self, *, reference: str,
                           provider_reference: str) -> ProviderResult:
        raise NotImplementedError


    def verify_webhook(self, *, body: bytes, headers: dict) -> bool:
        raise NotImplementedError

    def parse_webhook(self, *, body: bytes, headers: dict) -> WebhookEvent:
        raise NotImplementedError
