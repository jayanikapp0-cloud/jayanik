
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:

    def __init__(self) -> None:
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="jaynak-test-"))
        self.port = _free_port()
        env = {
            **os.environ,
            "PORT": str(self.port),
            "SESSION_SECRET": "test-only-secret-not-production",
            "DATA_FILE": str(self.dir / "data.json"),
            "SITE_DIR": str(self.dir / "site"),
            "DOCS_DIR": str(self.dir / "docs"),
            "ALLOW_DEV_CODE": "1",
            "OWNER_PHONE": "98061051",
        }
        for k in ("TWILIO_AUTH_TOKEN", "TWILIO_ACCOUNT_SID", "TWILIO_FROM",
                  "WHATSAPP_TOKEN", "ALLOWED_ORIGIN"):
            env.pop(k, None)
        self.proc = subprocess.Popen(
            [sys.executable, str(ROOT / "otp_server.py")],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(80):
            try:
                urllib.request.urlopen(self.base + "/health", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("لم يبدأ الخادم")

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def call(self, path, method="GET", body=None, token=None, raw=None, ctype=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body else None)
        req = urllib.request.Request(self.base + path, data=data, method=method)
        req.add_header("Content-Type", ctype or "application/json; charset=utf-8")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
        except urllib.error.HTTPError as e:
            raw_body = e.read()
            try:
                return e.code, json.loads(raw_body or b"{}"), dict(e.headers)
            except ValueError:
                return e.code, {"_raw": raw_body[:200].decode("utf-8", "replace")}, dict(e.headers)

    def login(self, phone: str) -> str:
        _, out, _ = self.call("/auth/request-otp", "POST", {"phone": phone})
        code = out.get("dev_code")
        assert code, f"لا رمز تطوير: {out}"
        _, out, _ = self.call("/auth/verify-otp", "POST", {"phone": phone, "code": code})
        assert out.get("token"), f"لا رمز جلسة: {out}"
        return out["token"]


SRV: Server


def setUpModule() -> None:
    global SRV
    SRV = Server()


def tearDownModule() -> None:
    SRV.stop()


class TestAuthentication(unittest.TestCase):

    def test_protected_endpoints_reject_anonymous(self):
        protected = [
            ("/api/me", "GET"), ("/api/orders", "GET"), ("/api/wallet", "GET"),
            ("/api/driver/feed", "GET"), ("/api/admin/overview", "GET"),
            ("/api/orders", "POST"), ("/api/driver/accept", "POST"),
            ("/api/driver/advance", "POST"), ("/api/driver/decline", "POST"),
            ("/api/driver/presence", "POST"), ("/api/orders/cancel", "POST"),
            ("/api/rewards/redeem", "POST"), ("/api/admin/approve", "POST"),
            ("/api/wallet/topup", "POST"), ("/documents/upload", "POST"),
        ]
        for path, method in protected:
            with self.subTest(path=path):
                status, out, _ = SRV.call(path, method, {} if method == "POST" else None)
                self.assertIn(status, (401, 403),
                              f"{path} أعاد {status} بلا مصادقة: {out}")

    def test_forged_signature_rejected(self):
        token = SRV.login("96890001111")
        payload, _sig = token.rsplit(".", 1)
        forged = payload + "." + "0" * 64
        status, _, _ = SRV.call("/api/me", token=forged)
        self.assertEqual(status, 401, "توقيع مزوّر قُبل")

    def test_expiry_cannot_be_extended_by_client(self):
        token = SRV.login("96890002222")
        phone, exp, rest = token.split(".", 2)
        iat, sig = rest.split(".", 1)
        tampered = f"{phone}.{int(exp) + 10_000_000}.{iat}.{sig}"
        status, _, _ = SRV.call("/api/me", token=tampered)
        self.assertEqual(status, 401, "تُقبل حمولة معدّلة")

    def test_logout_revokes_token(self):
        token = SRV.login("96890003333")
        self.assertEqual(SRV.call("/api/me", token=token)[0], 200)
        SRV.call("/auth/logout", "POST", token=token)
        self.assertEqual(SRV.call("/api/me", token=token)[0], 401,
                         "الرمز نجا من تسجيل الخروج")

    def test_otp_code_never_returned_without_explicit_optin(self):
        src = (ROOT / "otp_server.py").read_text(encoding="utf-8")
        self.assertIn('ALLOW_DEV_CODE = os.environ.get("ALLOW_DEV_CODE", "") == "1"', src,
                      "الرمز غير محصور خلف ضبط صريح")
        self.assertIn("if DEV_MODE and ALLOW_DEV_CODE:", src)

    def test_otp_attempts_are_limited(self):
        phone = "96890004444"
        SRV.call("/auth/request-otp", "POST", {"phone": phone})
        codes = ["0000", "1111", "2222", "3333", "4444", "5555"]
        statuses = [SRV.call("/auth/verify-otp", "POST",
                             {"phone": phone, "code": c})[0] for c in codes]
        self.assertIn(429, statuses, f"لا حدّ لمحاولات الرمز: {statuses}")

    def test_otp_requests_are_rate_limited(self):
        phone = "96890005555"
        statuses = [SRV.call("/auth/request-otp", "POST", {"phone": phone})[0]
                    for _ in range(6)]
        self.assertIn(429, statuses, f"لا حدّ لطلبات الرمز: {statuses}")


class TestAuthorization(unittest.TestCase):

    def test_cannot_read_another_users_order(self):
        a = SRV.login("96890010001")
        b = SRV.login("96890010002")
        _, out, _ = SRV.call("/api/orders", "POST", {
            "kind": "errand", "payment": "cash",
            "pickup": {"lat": 23.6178, "lon": 58.5636},
            "dropoff": {"lat": 23.5866, "lon": 58.3411},
        }, token=a)
        oid = out.get("order", {}).get("id")
        self.assertTrue(oid, f"لم يُنشأ الطلب: {out}")

        self.assertEqual(SRV.call(f"/api/orders/{oid}", token=a)[0], 200)
        status, _, _ = SRV.call(f"/api/orders/{oid}", token=b)
        self.assertEqual(status, 403, "مستخدم آخر قرأ الطلب")

    def test_normal_user_cannot_reach_admin(self):
        token = SRV.login("96890010003")
        for path, method in [("/api/admin/overview", "GET"),
                             ("/api/admin/approve", "POST"),
                             ("/api/admin/rules", "POST"),
                             ("/api/wallet/topup", "POST")]:
            with self.subTest(path=path):
                status, _, _ = SRV.call(path, method,
                                        {} if method == "POST" else None, token=token)
                self.assertEqual(status, 403, f"{path} مفتوح لمستخدم عادي")

    def test_cannot_cancel_another_users_order(self):
        a = SRV.login("96890010004")
        b = SRV.login("96890010005")
        _, out, _ = SRV.call("/api/orders", "POST", {
            "kind": "errand", "payment": "cash",
            "pickup": {"lat": 23.6178, "lon": 58.5636},
            "dropoff": {"lat": 23.5866, "lon": 58.3411},
        }, token=a)
        oid = out["order"]["id"]
        status, _, _ = SRV.call("/api/orders/cancel", "POST", {"orderID": oid}, token=b)
        self.assertNotEqual(status, 200, "مستخدم آخر ألغى الطلب")


class TestInputHandling(unittest.TestCase):

    def test_oversized_json_rejected(self):
        big = json.dumps({"a": "x" * 200_000}).encode()
        status, _, _ = SRV.call("/api/quote", "POST", raw=big)
        self.assertEqual(status, 413, "جسم ضخم لم يُرفض")

    def test_coordinates_outside_oman_rejected(self):
        token = SRV.login("96890020001")
        status, out, _ = SRV.call("/api/orders", "POST", {
            "kind": "errand", "payment": "cash",
            "pickup": {"lat": 48.85, "lon": 2.35},
            "dropoff": {"lat": 23.5866, "lon": 58.3411},
        }, token=token)
        self.assertEqual(status, 400, f"قُبلت إحداثيات خارج عمان: {out}")

    def test_unknown_kind_rejected(self):
        status, _, _ = SRV.call("/api/quote", "POST", {
            "pickup": {"lat": 23.6178, "lon": 58.5636},
            "dropoff": {"lat": 23.5866, "lon": 58.3411},
            "kind": "../../etc/passwd",
        })
        self.assertEqual(status, 400)

    def test_malformed_json_does_not_500(self):
        status, _, _ = SRV.call("/api/quote", "POST", raw=b"{not json")
        self.assertLess(status, 500, "JSON فاسد أسقط الخادم")


class TestBusinessRules(unittest.TestCase):
    """قواعد عمل قرّرها المالك — تُفحَص لا تُوثَّق فقط."""

    def _register(self, token, civil):
        return SRV.call("/api/driver/register", "POST", {
            "name": "سالم المعمري", "civilID": civil, "licenseNumber": "L-1",
            "zone": "بوشر",
            "vehicle": {"make": "تويوتا", "model": "هايلكس",
                        "plateNumber": "1234", "ownershipNumber": "9"},
        }, token=token)

    def test_civil_id_must_be_exactly_eight_digits(self):
        token = SRV.login("96890050001")
        for bad in ("1234567", "123456789", "12345678901", ""):
            with self.subTest(civil=bad):
                status, out, _ = self._register(token, bad)
                self.assertEqual(status, 400, f"قُبل رقم مدني بطول {len(bad)}: {out}")
                self.assertEqual(out.get("error"), "bad_civil")

    def test_civil_id_of_eight_digits_passes_length_check(self):
        token = SRV.login("96890050002")
        status, out, _ = self._register(token, "12345678")
        # قد يُرفض لأسباب أخرى، لكن **ليس** بسبب الطول
        self.assertNotEqual(out.get("error"), "bad_civil", out)

    def test_non_digits_are_stripped_not_accepted_as_length(self):
        """«1234-5678» ثمانية أرقام، و«abcdefgh» صفر."""
        token = SRV.login("96890050003")
        status, out, _ = self._register(token, "abcdefgh")
        self.assertEqual(out.get("error"), "bad_civil", out)


class TestFileUpload(unittest.TestCase):

    @staticmethod
    def _multipart(kind: bytes, filename: bytes, content: bytes) -> tuple[bytes, str]:
        b = b"----jaynaktest"
        body = (
            b"--" + b + b"\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\n"
            + kind + b"\r\n--" + b
            + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + filename
            + b"\"\r\nContent-Type: image/jpeg\r\n\r\n" + content + b"\r\n--" + b + b"--\r\n"
        )
        return body, f"multipart/form-data; boundary={b.decode()}"

    def test_upload_requires_session(self):
        body, ctype = self._multipart(b"civilIDFront", b"a.jpg", b"\xff\xd8\xff\xe0" + b"0" * 64)
        status, _, _ = SRV.call("/documents/upload", "POST", raw=body, ctype=ctype)
        self.assertIn(status, (401, 403))

    def test_disguised_executable_rejected(self):
        token = SRV.login("96890030001")
        body, ctype = self._multipart(b"civilIDFront", b"evil.jpg", b"#!/bin/sh\nrm -rf /\n")
        status, out, _ = SRV.call("/documents/upload", "POST", raw=body, ctype=ctype, token=token)
        self.assertNotEqual(status, 200, f"قُبل سكربت باسم صورة: {out}")

    def test_kind_path_traversal_rejected(self):
        token = SRV.login("96890030002")
        body, ctype = self._multipart(b"../../../etc/passwd", b"a.jpg",
                                      b"\xff\xd8\xff\xe0" + b"0" * 64)
        status, out, _ = SRV.call("/documents/upload", "POST", raw=body, ctype=ctype, token=token)
        self.assertNotEqual(status, 200, f"قُبل نوع بمسار خروج: {out}")

    def test_valid_image_accepted_and_path_not_leaked(self):
        token = SRV.login("96890030003")
        body, ctype = self._multipart(b"civilIDFront", b"id.jpg",
                                      b"\xff\xd8\xff\xe0" + b"0" * 2048)
        status, out, _ = SRV.call("/documents/upload", "POST", raw=body, ctype=ctype, token=token)
        self.assertEqual(status, 200, f"رُفضت صورة صالحة: {out}")
        blob = json.dumps(out, ensure_ascii=False)
        for leak in ("/Users", "/opt", "/tmp", "documents/"):
            self.assertNotIn(leak, blob, f"الرد يكشف مساراً: {blob}")


class TestStaticAndHeaders(unittest.TestCase):

    def test_web_root_cannot_escape(self):
        for path in ("/web/../otp_server.py", "/web/%2e%2e/otp_server.py",
                     "/web/../../etc/passwd"):
            with self.subTest(path=path):
                status, _, _ = SRV.call(path)
                self.assertEqual(status, 404, f"{path} لم يُرفض")

    def test_security_headers_present_on_api(self):
        _, _, headers = SRV.call("/health")
        for h in ("X-Content-Type-Options", "X-Frame-Options",
                  "Referrer-Policy", "Content-Security-Policy"):
            self.assertIn(h, headers, f"ترويسة {h} مفقودة")

    def test_cors_closed_by_default(self):
        _, _, headers = SRV.call("/health")
        self.assertNotIn("Access-Control-Allow-Origin", headers,
                         "CORS مفتوح افتراضياً")

    def test_errors_do_not_leak_internals(self):
        status, out, _ = SRV.call("/api/orders/does-not-exist", token=SRV.login("96890040001"))
        blob = json.dumps(out, ensure_ascii=False)
        for leak in ("Traceback", "File \"", "/Users/", "otp_server.py", "sqlite", "SELECT"):
            self.assertNotIn(leak, blob, f"تسريب داخلي: {blob}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
