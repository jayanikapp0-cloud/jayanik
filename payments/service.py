
from __future__ import annotations

import logging
import secrets
import threading
import time

from .models import (
    Currency, Payment, PaymentAttempt, PaymentError, PaymentStatus,
    can_transition, new_reference,
)
from .provider import PaymentProvider, WebhookEvent

log = logging.getLogger("jaynak.payments")


class PaymentService:

    def __init__(self, store, provider: PaymentProvider | None = None) -> None:
        self._store = store
        self._provider = provider
        self._lock = threading.RLock()


    @staticmethod
    def _all(data: dict) -> list:
        return data.setdefault("payments", [])

    @staticmethod
    def _find(data: dict, **by) -> dict | None:
        for row in data.setdefault("payments", []):
            if all(row.get(k) == v for k, v in by.items()):
                return row
        return None

    @property
    def provider(self) -> PaymentProvider:
        if self._provider is None:


            raise PaymentError("no_provider", "لم تُربط بوّابة دفع بعد")
        return self._provider

    @property
    def has_provider(self) -> bool:
        return self._provider is not None


    def create_payment(self, *, order_id: str, customer_phone: str,
                       amount_baisa: int, idempotency_key: str,
                       currency: Currency = Currency.OMR) -> Payment:
        if amount_baisa <= 0:
            raise PaymentError("invalid_amount", "مبلغ غير صالح")
        if not idempotency_key or len(idempotency_key) < 8:
            raise PaymentError("invalid_idempotency_key", "مفتاح تفرّد غير صالح")

        def run(data):
            existing = self._find(data, idempotency_key=idempotency_key)
            if existing:

                if (existing["order_id"] != order_id
                        or existing["amount_baisa"] != amount_baisa):
                    return {"error": "idempotency_conflict"}
                return {"payment": existing, "reused": True}


            for row in self._all(data):
                if row["order_id"] == order_id and row["status"] in (
                        PaymentStatus.PAID.value, PaymentStatus.PROCESSING.value):
                    return {"error": "already_paying"}

            p = Payment(
                payment_id=secrets.token_hex(12),
                reference=new_reference(),
                order_id=order_id,
                customer_phone=customer_phone,
                amount_baisa=amount_baisa,
                currency=currency,
                idempotency_key=idempotency_key,
                provider=self._provider.name if self._provider else None,
            )
            self._all(data).append(p.to_dict())
            return {"payment": p.to_dict(), "reused": False}

        out = self._store.mutate(run)
        if "error" in out:
            raise PaymentError(out["error"], self._message(out["error"]))
        if not out["reused"]:
            log.info("payment.created ref=%s order=%s amount_baisa=%d",
                     out["payment"]["reference"], order_id, amount_baisa)
        return Payment.from_dict(out["payment"])


    def initialize(self, reference: str) -> Payment:
        payment = self.get(reference)
        result = self.provider.create_payment(
            reference=payment.reference,
            amount_baisa=payment.amount_baisa,
            currency=payment.currency.value,
            customer_phone=payment.customer_phone,
            order_id=payment.order_id,
        )
        return self._apply(
            reference,
            result.status if result.status != PaymentStatus.PENDING
            else PaymentStatus.PROCESSING,
            provider_reference=result.provider_reference,
            attempt=PaymentAttempt(
                attempt_id=secrets.token_hex(8),
                provider=self.provider.name,
                status=result.status,
                provider_reference=result.provider_reference,
                error_code=result.error_code,
                error_message=result.error_message,
            ),
        )

    def verify(self, reference: str) -> Payment:
        payment = self.get(reference)
        if not payment.provider_reference:
            raise PaymentError("not_initialized", "لم تبدأ الدفعة بعد")
        result = self.provider.verify_payment(
            reference=payment.reference,
            provider_reference=payment.provider_reference,
        )
        return self._apply(reference, result.status,
                           provider_reference=result.provider_reference)

    def refund(self, reference: str, *, amount_baisa: int | None = None,
               reason: str = "") -> Payment:
        payment = self.get(reference)
        if payment.status != PaymentStatus.PAID:
            raise PaymentError("not_refundable", "لا يمكن استرداد دفعة غير مدفوعة")
        amount = amount_baisa or payment.amount_baisa
        if amount > payment.amount_baisa - payment.refunded_baisa:
            raise PaymentError("refund_too_large", "مبلغ الاسترداد أكبر من المتبقّي")
        result = self.provider.refund_payment(
            reference=payment.reference,
            provider_reference=payment.provider_reference or "",
            amount_baisa=amount, reason=reason,
        )
        if result.status == PaymentStatus.FAILED:
            raise PaymentError("refund_failed", result.error_message or "فشل الاسترداد")
        return self._apply(reference, PaymentStatus.REFUNDED, refunded=amount)

    def cancel(self, reference: str) -> Payment:
        payment = self.get(reference)
        if payment.status not in (PaymentStatus.PENDING, PaymentStatus.PROCESSING):
            raise PaymentError("not_cancellable", "لا يمكن إلغاء هذه الدفعة")
        if payment.provider_reference and self.has_provider:
            self.provider.cancel_payment(
                reference=payment.reference,
                provider_reference=payment.provider_reference,
            )
        return self._apply(reference, PaymentStatus.CANCELLED)


    def handle_webhook(self, *, body: bytes, headers: dict) -> dict:
        if not self.provider.verify_webhook(body=body, headers=headers):
            log.warning("webhook.bad_signature bytes=%d", len(body))
            raise PaymentError("bad_signature", "توقيع غير صالح")

        event: WebhookEvent = self.provider.parse_webhook(body=body, headers=headers)
        if not event.event_id or not event.reference:
            raise PaymentError("bad_event", "حدث ناقص")

        def run(data):
            row = self._find(data, reference=event.reference)
            if not row:
                return {"error": "unknown_reference"}


            if event.event_id in row.setdefault("processed_events", []):
                return {"duplicate": True, "status": row["status"]}

            if event.amount_baisa is not None and event.amount_baisa != row["amount_baisa"]:
                return {"error": "amount_mismatch"}

            current = PaymentStatus(row["status"])
            if not can_transition(current, event.status):
                log.warning("webhook.illegal_transition ref=%s %s->%s",
                            event.reference, current.value, event.status.value)
                row["processed_events"].append(event.event_id)
                return {"ignored": True, "status": row["status"]}

            row["status"] = event.status.value
            row["provider_reference"] = event.provider_reference or row.get("provider_reference")
            row["processed_events"].append(event.event_id)
            row["updated_at"] = time.time()
            return {"ok": True, "status": row["status"]}

        out = self._store.mutate(run)
        if "error" in out:
            raise PaymentError(out["error"], self._message(out["error"]))
        log.info("webhook.processed ref=%s event=%s result=%s",
                 event.reference, event.event_id, out)
        return out


    def get(self, reference: str) -> Payment:
        row = self._find(self._store.get(), reference=reference)
        if not row:
            raise PaymentError("not_found", "الدفعة غير موجودة")
        return Payment.from_dict(row)

    def for_order(self, order_id: str) -> list[Payment]:
        return [Payment.from_dict(r) for r in self._all(self._store.get())
                if r["order_id"] == order_id]


    def _apply(self, reference: str, target: PaymentStatus, *,
               provider_reference: str | None = None,
               attempt: PaymentAttempt | None = None,
               refunded: int | None = None) -> Payment:
        def run(data):
            row = self._find(data, reference=reference)
            if not row:
                return {"error": "not_found"}
            current = PaymentStatus(row["status"])
            if not can_transition(current, target):
                return {"error": "illegal_transition"}
            row["status"] = target.value
            if provider_reference:
                row["provider_reference"] = provider_reference
            if attempt:
                row.setdefault("attempts", []).append(attempt.to_dict())
            if refunded:
                row["refunded_baisa"] = row.get("refunded_baisa", 0) + refunded
            row["updated_at"] = time.time()
            return {"payment": row}

        out = self._store.mutate(run)
        if "error" in out:
            raise PaymentError(out["error"], self._message(out["error"]))
        log.info("payment.status ref=%s -> %s", reference, target.value)
        return Payment.from_dict(out["payment"])

    @staticmethod
    def _message(code: str) -> str:
        return {
            "idempotency_conflict": "طلب مكرّر ببيانات مختلفة",
            "already_paying": "هناك دفعة جارية لهذا الطلب",
            "not_found": "الدفعة غير موجودة",
            "illegal_transition": "انتقال حالة غير مسموح",
            "unknown_reference": "مرجع غير معروف",
            "amount_mismatch": "المبلغ لا يطابق الدفعة",
            "bad_signature": "توقيع غير صالح",
        }.get(code, "تعذّر إتمام العملية")
