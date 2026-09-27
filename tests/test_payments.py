
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from payments import (
    Payment, PaymentError, PaymentProvider, PaymentService, PaymentStatus,
    ProviderResult, WebhookEvent,
)

SECRET = b"webhook-test-secret"


class MemoryStore:

    def __init__(self) -> None:
        self._data: dict = {"payments": []}
        self._lock = threading.RLock()

    def get(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def mutate(self, fn):
        with self._lock:
            result = fn(self._data)
            return json.loads(json.dumps(result))


class FakeProvider(PaymentProvider):

    name = "fake"

    def __init__(self) -> None:
        self.created = 0
        self.next_status = PaymentStatus.PROCESSING

    def create_payment(self, *, reference, amount_baisa, currency,
                       customer_phone, order_id, return_url=None):
        self.created += 1
        return ProviderResult(status=PaymentStatus.PROCESSING,
                              provider_reference=f"prov-{reference}")

    def verify_payment(self, *, reference, provider_reference):
        return ProviderResult(status=self.next_status,
                              provider_reference=provider_reference)

    def refund_payment(self, *, reference, provider_reference, amount_baisa, reason=""):
        return ProviderResult(status=PaymentStatus.REFUNDED,
                              provider_reference=provider_reference)

    def cancel_payment(self, *, reference, provider_reference):
        return ProviderResult(status=PaymentStatus.CANCELLED)

    def verify_webhook(self, *, body, headers):
        sent = headers.get("X-Signature", "")
        expected = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(sent, expected)

    def parse_webhook(self, *, body, headers):
        d = json.loads(body)
        return WebhookEvent(
            event_id=d["id"], reference=d["reference"],
            status=PaymentStatus(d["status"]),
            provider_reference=d.get("provider_reference"),
            amount_baisa=d.get("amount_baisa"),
        )


def signed(payload: dict) -> tuple[bytes, dict]:
    body = json.dumps(payload).encode()
    return body, {"X-Signature": hmac.new(SECRET, body, hashlib.sha256).hexdigest()}


class TestIdempotency(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.provider = FakeProvider()
        self.svc = PaymentService(self.store, self.provider)

    def test_double_tap_creates_one_payment(self):
        a = self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=4578, idempotency_key="key-abcdef12")
        b = self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=4578, idempotency_key="key-abcdef12")
        self.assertEqual(a.reference, b.reference)
        self.assertEqual(len(self.store.get()["payments"]), 1)

    def test_same_key_different_amount_is_rejected(self):
        self.svc.create_payment(order_id="o1", customer_phone="968",
                                amount_baisa=4578, idempotency_key="key-abcdef12")
        with self.assertRaises(PaymentError) as e:
            self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=9999, idempotency_key="key-abcdef12")
        self.assertEqual(e.exception.code, "idempotency_conflict")

    def test_second_payment_for_same_order_blocked(self):
        p = self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=4578, idempotency_key="key-aaaaaaa1")
        self.svc.initialize(p.reference)
        with self.assertRaises(PaymentError) as e:
            self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=4578, idempotency_key="key-bbbbbbb2")
        self.assertEqual(e.exception.code, "already_paying")

    def test_weak_idempotency_key_rejected(self):
        with self.assertRaises(PaymentError):
            self.svc.create_payment(order_id="o1", customer_phone="968",
                                    amount_baisa=100, idempotency_key="x")


class TestStateMachine(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.provider = FakeProvider()
        self.svc = PaymentService(self.store, self.provider)
        self.p = self.svc.create_payment(order_id="o1", customer_phone="968",
                                         amount_baisa=4578,
                                         idempotency_key="key-cccccccc")

    def test_server_confirms_not_client(self):
        self.svc.initialize(self.p.reference)
        self.provider.next_status = PaymentStatus.PAID
        after = self.svc.verify(self.p.reference)
        self.assertEqual(after.status, PaymentStatus.PAID)

    def test_refunded_cannot_go_back_to_paid(self):
        self.svc.initialize(self.p.reference)
        self.provider.next_status = PaymentStatus.PAID
        self.svc.verify(self.p.reference)
        self.svc.refund(self.p.reference)
        body, headers = signed({"id": "evt-late", "reference": self.p.reference,
                                "status": "PAID"})
        out = self.svc.handle_webhook(body=body, headers=headers)
        self.assertTrue(out.get("ignored"), out)
        self.assertEqual(self.svc.get(self.p.reference).status, PaymentStatus.REFUNDED)

    def test_failed_is_terminal(self):
        body, headers = signed({"id": "e1", "reference": self.p.reference,
                                "status": "FAILED"})
        self.svc.handle_webhook(body=body, headers=headers)
        body2, headers2 = signed({"id": "e2", "reference": self.p.reference,
                                  "status": "PAID"})
        out = self.svc.handle_webhook(body=body2, headers=headers2)
        self.assertTrue(out.get("ignored"))
        self.assertEqual(self.svc.get(self.p.reference).status, PaymentStatus.FAILED)

    def test_cannot_refund_unpaid(self):
        with self.assertRaises(PaymentError) as e:
            self.svc.refund(self.p.reference)
        self.assertEqual(e.exception.code, "not_refundable")


class TestWebhooks(unittest.TestCase):

    def setUp(self):
        self.store = MemoryStore()
        self.provider = FakeProvider()
        self.svc = PaymentService(self.store, self.provider)
        self.p = self.svc.create_payment(order_id="o1", customer_phone="968",
                                         amount_baisa=4578,
                                         idempotency_key="key-dddddddd")
        self.svc.initialize(self.p.reference)

    def test_forged_signature_rejected(self):
        body = json.dumps({"id": "e1", "reference": self.p.reference,
                           "status": "PAID"}).encode()
        with self.assertRaises(PaymentError) as e:
            self.svc.handle_webhook(body=body, headers={"X-Signature": "00" * 32})
        self.assertEqual(e.exception.code, "bad_signature")
        self.assertEqual(self.svc.get(self.p.reference).status, PaymentStatus.PROCESSING)

    def test_duplicate_webhook_applied_once(self):
        body, headers = signed({"id": "evt-1", "reference": self.p.reference,
                                "status": "PAID"})
        first = self.svc.handle_webhook(body=body, headers=headers)
        second = self.svc.handle_webhook(body=body, headers=headers)
        self.assertTrue(first.get("ok"))
        self.assertTrue(second.get("duplicate"))
        row = self.store.get()["payments"][0]
        self.assertEqual(row["processed_events"].count("evt-1"), 1)

    def test_amount_mismatch_rejected(self):
        body, headers = signed({"id": "e9", "reference": self.p.reference,
                                "status": "PAID", "amount_baisa": 1})
        with self.assertRaises(PaymentError) as e:
            self.svc.handle_webhook(body=body, headers=headers)
        self.assertEqual(e.exception.code, "amount_mismatch")

    def test_unknown_reference_rejected(self):
        body, headers = signed({"id": "e1", "reference": "JNP-nope", "status": "PAID"})
        with self.assertRaises(PaymentError) as e:
            self.svc.handle_webhook(body=body, headers=headers)
        self.assertEqual(e.exception.code, "unknown_reference")


class TestNoProvider(unittest.TestCase):

    def test_clear_error_before_any_gateway_is_wired(self):
        svc = PaymentService(MemoryStore(), None)
        p = svc.create_payment(order_id="o1", customer_phone="968",
                               amount_baisa=100, idempotency_key="key-eeeeeeee")
        with self.assertRaises(PaymentError) as e:
            svc.initialize(p.reference)
        self.assertEqual(e.exception.code, "no_provider")


class TestNoCardData(unittest.TestCase):

    def test_model_has_no_card_fields(self):
        fields = set(Payment("i", "r", "o", "p", 1).to_dict().keys())
        forbidden = {"pan", "card_number", "cvv", "cvc", "expiry",
                     "card", "security_code", "track_data"}
        self.assertEqual(fields & forbidden, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
