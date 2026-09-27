

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


import core
import store

SUPPORT_PHONE = os.environ.get("SUPPORT_PHONE", "+968 98169943")
PORT = int(os.environ.get("PORT", "8787"))
COUNTRY_CODE = os.environ.get("COUNTRY_CODE", "968")
NATIONAL_LEN = int(os.environ.get("NATIONAL_LEN", "8"))
CODE_LEN = int(os.environ.get("CODE_LEN", "4"))

CIVIL_ID_LEN = int(os.environ.get("CIVIL_ID_LEN", "8"))
CODE_TTL = int(os.environ.get("CODE_TTL", "300"))
MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))
RESEND_COOLDOWN = int(os.environ.get("RESEND_COOLDOWN", "45"))


PROVIDER = os.environ.get("OTP_PROVIDER", "").strip().lower()

WA_TOKEN = os.environ.get("WHATSAPP_TOKEN", "").strip()
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
WA_TEMPLATE = os.environ.get("WHATSAPP_TEMPLATE", "otp_verification").strip()
WA_LANG = os.environ.get("WHATSAPP_LANG", "ar").strip()
WA_API_VERSION = os.environ.get("WHATSAPP_API_VERSION", "v21.0").strip()


SESSION_SECRET = os.environ.get("SESSION_SECRET", secrets.token_hex(32)).encode()
SESSION_TTL = int(os.environ.get("SESSION_TTL", str(60 * 60 * 24 * 30)))


TW_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TW_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TW_FROM = os.environ.get("TWILIO_FROM", "").strip()


IB_KEY = os.environ.get("INFOBIP_API_KEY", "").strip()
IB_HOST = os.environ.get("INFOBIP_HOST", "").strip()
IB_FROM = os.environ.get("INFOBIP_FROM", "Jaynak").strip()


UF_APPSID = os.environ.get("UNIFONIC_APPSID", "").strip()
UF_FROM = os.environ.get("UNIFONIC_SENDER", "Jaynak").strip()


TG_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

MESSAGE_TEXT = os.environ.get(
    "OTP_TEXT", "رمز التحقّق في جاينك: {code}\nصالح ٥ دقائق. لا تشاركه مع أحد.")


def detect_provider() -> str:
    if PROVIDER:
        return PROVIDER
    if WA_TOKEN and WA_PHONE_ID:
        return "whatsapp"
    if TW_SID and TW_TOKEN and TW_FROM:
        return "twilio"
    if IB_KEY and IB_HOST:
        return "infobip"
    if UF_APPSID:
        return "unifonic"
    if TG_TOKEN:
        return "telegram"
    return "console"


ACTIVE_PROVIDER = detect_provider()
DEV_MODE = ACTIVE_PROVIDER == "console"


ALLOW_DEV_CODE = os.environ.get("ALLOW_DEV_CODE", "") == "1"


_lock = threading.Lock()
_challenges: dict[str, dict] = {}
_phone_hits: dict[str, list] = {}
_ip_hits: dict[str, list] = {}

PHONE_LIMIT, PHONE_WINDOW = 3, 15 * 60
IP_LIMIT, IP_WINDOW = 20, 60 * 60


def normalize(raw: str) -> str | None:
    d = re.sub(r"\D", "", raw or "")
    if len(d) == NATIONAL_LEN:
        d = COUNTRY_CODE + d
    if not d.startswith(COUNTRY_CODE):
        return None
    if len(d) != len(COUNTRY_CODE) + NATIONAL_LEN:
        return None
    return d


def rate_limited(bucket: dict, key: str, limit: int, window: int) -> bool:
    now = time.time()
    hits = [t for t in bucket.get(key, []) if now - t < window]
    bucket[key] = hits
    return len(hits) >= limit


def record_hit(bucket: dict, key: str) -> None:
    bucket.setdefault(key, []).append(time.time())


def hash_code(code: str, salt: bytes) -> bytes:


    return hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 120_000)


def issue_session(phone: str) -> str:
    now = int(time.time())
    exp = now + SESSION_TTL
    issued_ms = int(time.time() * 1000)


    payload = f"{phone}.{exp}.{issued_ms}"
    sig = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session(header: str | None) -> str | None:
    if not header or not header.startswith("Bearer "):
        return None
    token = header[7:].strip()
    parts = token.rsplit(".", 1)
    if len(parts) != 2:
        return None
    payload, sig = parts
    expected = hmac.new(SESSION_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        phone, exp, iat = payload.split(".", 2)
        if int(exp) < int(time.time()):
            return None

        if int(iat) <= _revoked.get(phone, 0):
            return None
    except ValueError:
        return None
    return phone


SITE_DIR = pathlib.Path(os.environ.get("SITE_DIR", "./site-data")).resolve()
CONTENT_FILE = SITE_DIR / "content.json"
NOTIFY_FILE = SITE_DIR / "notify.json"
ACCOUNTS_FILE = SITE_DIR / "accounts.json"


OWNER_PHONE = normalize(os.environ.get("OWNER_PHONE", "98061051")) or ""


ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "").strip()


MAX_JSON_BODY = int(os.environ.get("MAX_JSON_BODY", str(64 * 1024)))

DOCS_DIR = pathlib.Path(os.environ.get("DOCS_DIR", "./documents")).resolve()


_REVOKE_FILE = SITE_DIR / "sessions.json"
_revoke_lock = threading.Lock()


def _load_revocations() -> dict:
    try:
        raw = json.loads(_REVOKE_FILE.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except (OSError, ValueError, AttributeError):
        return {}


_revoked: dict = _load_revocations()


def revoke_sessions(phone: str) -> None:
    with _revoke_lock:


        _revoked[phone] = int(time.time() * 1000)
        try:
            SITE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = _REVOKE_FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps(_revoked), encoding="utf-8")
            tmp.replace(_REVOKE_FILE)
            os.chmod(_REVOKE_FILE, 0o600)
        except OSError:

            print("  ✗ تعذّر حفظ إبطال الجلسات", flush=True)

MAX_UPLOAD = 8 * 1024 * 1024
ALLOWED = {
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"\x89PNG": ("image/png", "png"),
    b"%PDF": ("application/pdf", "pdf"),
}
DOC_KINDS = {"civilIDFront", "civilIDBack", "license", "ownership", "vehicle"}


def sniff_type(blob: bytes) -> tuple[str, str] | None:
    for magic, kind in ALLOWED.items():
        if blob.startswith(magic):
            return kind
    return None


def owner_dir(owner_key: str) -> pathlib.Path:
    digest = hashlib.sha256((owner_key + SESSION_SECRET.decode(errors="ignore")).encode()).hexdigest()[:32]
    d = DOCS_DIR / digest
    d.mkdir(parents=True, exist_ok=True)
    return d


def parse_multipart(body: bytes, boundary: bytes) -> dict:
    out = {"fields": {}, "file": None, "declared_type": ""}
    sep = b"--" + boundary
    for part in body.split(sep):
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if b"\r\n\r\n" not in part:
            continue
        head, payload = part.split(b"\r\n\r\n", 1)
        payload = payload.rstrip(b"\r\n")
        headers = head.decode("utf-8", "replace")
        if 'name="file"' in headers:
            out["file"] = payload
            for line in headers.splitlines():
                if line.lower().startswith("content-type:"):
                    out["declared_type"] = line.split(":", 1)[1].strip()
        else:
            for line in headers.splitlines():
                if 'name="' in line:
                    name = line.split('name="', 1)[1].split('"', 1)[0]
                    out["fields"][name] = payload.decode("utf-8", "replace")
                    break
    return out


def _post(url: str, data: bytes, headers: dict) -> tuple[bool, str]:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return True, r.read().decode()[:300]
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:400]
        print(f"  ✗ المزوّد رفض ({e.code}): {detail}", flush=True)
        return False, detail
    except Exception as e:
        print(f"  ✗ تعذّر الاتصال بالمزوّد: {e}", flush=True)
        return False, str(e)


def send_sms_twilio(phone: str, text: str) -> tuple[bool, str]:
    import base64
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TW_SID}/Messages.json"
    body = urllib.parse.urlencode({"To": f"+{phone}", "From": TW_FROM, "Body": text})
    auth = base64.b64encode(f"{TW_SID}:{TW_TOKEN}".encode()).decode()
    return _post(url, body.encode(), {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    })


def send_sms_infobip(phone: str, text: str) -> tuple[bool, str]:
    url = f"https://{IB_HOST}/sms/2/text/advanced"
    body = {"messages": [{"from": IB_FROM, "destinations": [{"to": phone}], "text": text}]}
    return _post(url, json.dumps(body).encode(), {
        "Authorization": f"App {IB_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })


def send_sms_unifonic(phone: str, text: str) -> tuple[bool, str]:
    url = "https://el.cloud.unifonic.com/rest/SMS/messages"
    body = urllib.parse.urlencode({
        "AppSid": UF_APPSID, "Recipient": phone,
        "Body": text, "SenderID": UF_FROM,
    })
    return _post(url, body.encode(),
                 {"Content-Type": "application/x-www-form-urlencoded"})


def send_telegram(phone: str, text: str) -> tuple[bool, str]:
    chat_id = os.environ.get(f"TG_CHAT_{phone}", "").strip()
    if not chat_id:
        return False, "لا يوجد chat_id مربوط بهذا الرقم"
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text}).encode()
    return _post(url, body, {"Content-Type": "application/json"})


def send_whatsapp(phone: str, code: str) -> tuple[bool, str]:
    text = MESSAGE_TEXT.format(code=code)

    if ACTIVE_PROVIDER == "console":
        print(f"\n  [وضع التطوير] الرمز لـ +{phone} هو: {code}\n", flush=True)
        return True, "dev"
    if ACTIVE_PROVIDER == "twilio":
        return send_sms_twilio(phone, text)
    if ACTIVE_PROVIDER == "infobip":
        return send_sms_infobip(phone, text)
    if ACTIVE_PROVIDER == "unifonic":
        return send_sms_unifonic(phone, text)
    if ACTIVE_PROVIDER == "telegram":
        return send_telegram(phone, text)


    return send_whatsapp_template(phone, code)


WA_HINTS = {
    131030: "الرقم غير مضاف في قائمة المستلمين. في وضع التجربة أضفه من "
            "WhatsApp ← API Setup ← To، وأكّده بالرمز.",
    132001: "القالب غير موجود. تأكد من اسم القالب واللغة "
            "(WHATSAPP_TEMPLATE و WHATSAPP_LANG) ومن أنه اعتُمد.",
    132000: "عدد معاملات القالب لا يطابق ما أرسلناه — راجع تعريف القالب.",
    132005: "نص القالب غير مطابق للمعتمد.",
    132015: "القالب موقوف أو مرفوض من ميتا.",
    190:    "التوكن منتهي. المؤقّت يعيش ٢٤ ساعة — أنشئ System User token دائماً.",
    133010: "الرقم غير مسجّل في حساب واتساب أعمال.",
    100:    "معامل غير صالح — غالباً Phone number ID خطأ.",
    131056: "محاولات كثيرة لنفس الرقم خلال وقت قصير.",
    368:    "الحساب مقيّد مؤقتاً من ميتا.",
}


def explain_wa_error(detail: str) -> str:
    try:
        err = json.loads(detail).get("error", {})
        code = err.get("code")
        msg = err.get("message", "")
        hint = WA_HINTS.get(code)
        return f"[{code}] {hint or msg}"
    except Exception:
        return detail[:200]


def send_whatsapp_template(phone: str, code: str) -> tuple[bool, str]:
    url = f"https://graph.facebook.com/{WA_API_VERSION}/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}",
               "Content-Type": "application/json"}

    body_component = {"type": "body", "parameters": [{"type": "text", "text": code}]}
    button_component = {"type": "button", "sub_type": "url", "index": "0",
                        "parameters": [{"type": "text", "text": code}]}

    attempts = [[body_component, button_component], [body_component]]
    if os.environ.get("WHATSAPP_WITH_BUTTON", "1") != "1":
        attempts.reverse()

    last = ""
    for components in attempts:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": WA_TEMPLATE,
                "language": {"code": WA_LANG},
                "components": components,
            },
        }
        ok, detail = _post(url, json.dumps(payload).encode(), headers)
        if ok:
            return True, detail
        last = detail

        if not any(str(c) in detail for c in (132000, 132005, 131008)):
            break

    return False, explain_wa_error(last)


_site_lock = threading.Lock()


DEFAULT_CONTENT = {
    "hero": {
        "eyebrow": "نغطّي جميع ولايات سلطنة عُمان",
        "title1": "وين نوصل",
        "title2": "لك اليوم؟",
        "lead": ("جاينك يوصّل مستنداتك وطرودك ومشاويرك في أي ولاية بالسلطنة. "
                 "اطلب مندوب، شوف السعر قبل ما تأكّد، وتابعه على الخريطة لحظة بلحظة."),
        "note": "التطبيق قريباً على App Store و Google Play — سجّل الآن ونبلّغك أول ما ينزل.",
    },
    "contact": {
        "phone": "+968 98169943",
        "email": "jayanikapp@gmail.com",
        "whatsapp": "96898169943",
        "area": "جميع ولايات سلطنة عُمان",
    },
    "notify": {
        "waMessage": ("السلام عليكم، أبغى تذكير أوّل ما ينزل تطبيق جاينك على المتاجر.\n"
                      "رقمي: {phone}"),
        "confirm": "ترقّب الإعلان قريباً — بنذكّرك أوّل ما ينزل التطبيق.",
    },
    "loyalty": {
        "pointsPerOrder": 10,
        "pointsPerRial": 10,
        "cancelPenalty": 20,
    },
    "stores": {
        "ios":     {"url": "", "available": False},
        "android": {"url": "", "available": False},
    },
    # تسعيرة ثابتة: مبلغ واحد لكل طلب مهما كانت المسافة أو الولاية.
    "pricing": {
        "flatFare": 2,
    },
    "zones": [
        {"name": "بوشر", "fee": 0.0},      {"name": "مطرح", "fee": 0.1},
        {"name": "السيب", "fee": 0.15},    {"name": "مسقط", "fee": 0.2},
        {"name": "العامرات", "fee": 0.3},  {"name": "بركاء", "fee": 0.6},
        {"name": "قريات", "fee": 0.7},     {"name": "صحار", "fee": 1.5},
        {"name": "نزوى", "fee": 1.8},      {"name": "صلالة", "fee": 2.5},
    ],
}


def _read_json(path: pathlib.Path, fallback):
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return fallback


def _write_json(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.replace(path.with_suffix(path.suffix + ".bak"))
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def load_content() -> dict:
    with _site_lock:
        return _read_json(CONTENT_FILE, DEFAULT_CONTENT)


def merge_content(patch: dict) -> dict:
    def deep(base, over):
        out = dict(base)
        for k, v in over.items():
            out[k] = deep(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
        return out

    with _site_lock:
        current = _read_json(CONTENT_FILE, DEFAULT_CONTENT)
        merged = deep(current, patch)
        _write_json(CONTENT_FILE, merged)
        return merged


def append_record(path: pathlib.Path, record: dict, key: str) -> bool:
    with _site_lock:
        rows = _read_json(path, [])
        if not isinstance(rows, list):
            rows = []
        if any(r.get(key) == record.get(key) for r in rows):
            return False
        rows.append(record)
        _write_json(path, rows)
        return True


class Handler(BaseHTTPRequestHandler):
    server_version = "JaynakOTP/1.0"


    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"  {self.address_string()} - {fmt % args}", flush=True)

    def _cors(self):

        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if not ALLOWED_ORIGIN:
            return
        self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")
        self.send_header("Access-Control-Max-Age", "600")


    ERROR_STATUS = {
        "unauthorized": 401,
        "forbidden": 403,
        "not_yours": 403,
        "not_approved": 403,
        "barred": 403,
        "blocked": 403,
        "not_found": 404,
        "taken": 409,
        "balance": 409,
        "not_enough": 409,
        "insufficient_funds": 409,
        "already": 409,
        "payload_too_large": 413,
        "too_many": 429,
        "cooldown": 429,
        "send_failed": 502,
    }

    def _respond(self, out: dict, ok_status: int = 200):
        if "error" in out:
            return self._json(self.ERROR_STATUS.get(out["error"], 400), out)
        return self._json(ok_status, out)

    def _json(self, status: int, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return {}
        if n > MAX_JSON_BODY:
            self._oversized = True
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"ok": True, "mode": "dev" if DEV_MODE else ACTIVE_PROVIDER})
        elif self.path == "/site/content":
            self._json(200, load_content())
        elif self.path == "/site/me":
            phone = verify_session(self.headers.get("Authorization"))
            self._json(200, {"phone": phone, "isOwner": bool(phone) and phone == OWNER_PHONE})
        elif self.path == "/api/places":
            self._json(200, {"places": core.PLACES, "zones": core.ZONES,
                             "center": {"lat": core.CITY_CENTER[0],
                                        "lon": core.CITY_CENTER[1]},
                             "kinds": [{"id": k, "title": t,
                                        "multiplier": core.KIND_MULTIPLIER[k]}
                                       for k, t in core.KIND_TITLE.items()]})
        elif self.path == "/api/me":
            self.api_me()
        elif self.path == "/api/orders":
            self.api_list_orders()
        elif self.path == "/api/driver/feed":
            self.api_driver_feed()
        elif self.path == "/api/admin/overview":
            self.api_admin_overview()
        elif self.path == "/api/wallet":
            self.api_wallet()
        elif self.path.startswith("/api/orders/"):
            self.api_order_detail(self.path.rsplit("/", 1)[-1].split("?")[0])
        elif self.path.split("?", 1)[0] == "/sw.js":


            self.serve_web("sw.js")
        elif self.path.split("?", 1)[0] in ("/", "/index.html", "/app"):
            self.serve_web("index.html")
        elif self.path.split("?", 1)[0].startswith("/web/"):
            self.serve_web(self.path.split("?", 1)[0][5:])
        else:
            self._json(404, {"error": "not_found"})

    def do_PUT(self):
        if self.path == "/site/content":
            self.save_content()
        else:
            self._json(404, {"error": "not_found"})

    def _reject_oversized(self) -> bool:
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return False

        if self.path == "/documents/upload":
            return False
        if n > MAX_JSON_BODY:
            self._json(413, {"error": "payload_too_large",
                             "message": "حجم الطلب كبير جداً"})
            return True
        return False

    def do_POST(self):
        if self._reject_oversized():
            return
        if self.path == "/auth/request-otp":
            self.request_otp()
        elif self.path == "/auth/verify-otp":
            self.verify_otp()
        elif self.path == "/documents/upload":
            self.upload_document()
        elif self.path == "/auth/logout":
            self.logout()
        elif self.path == "/auth/register":
            self.register()
        elif self.path == "/site/signup":
            self.web_signup()
        elif self.path == "/site/notify":
            self.notify_signup()
        elif self.path == "/api/quote":
            self.api_quote()
        elif self.path == "/api/orders":
            self.api_create_order()
        elif self.path == "/api/driver/presence":
            self.api_driver_presence()
        elif self.path == "/api/driver/accept":
            self.api_driver_accept()
        elif self.path == "/api/driver/advance":
            self.api_driver_advance()
        elif self.path == "/api/admin/approve":
            self.api_admin_approve()
        elif self.path == "/api/admin/rules":
            self.api_admin_rules()
        elif self.path == "/api/wallet/topup":
            self.api_wallet_topup()
        elif self.path == "/api/rewards/redeem":
            self.api_redeem()
        elif self.path == "/api/orders/rate":
            self.api_rate()
        elif self.path == "/api/orders/cancel":
            self.api_cancel()
        elif self.path == "/api/driver/register":
            self.api_driver_register()
        elif self.path == "/api/driver/destination":
            self.api_driver_destination()
        elif self.path == "/api/driver/decline":
            self.api_driver_decline()
        else:
            self._json(404, {"error": "not_found"})


    def save_content(self):
        phone = verify_session(self.headers.get("Authorization"))
        if not phone:
            return self._json(401, {"error": "unauthorized",
                                    "message": "الجلسة غير صالحة، أعد تسجيل الدخول"})
        if phone != OWNER_PHONE:
            return self._json(403, {"error": "forbidden",
                                    "message": "هذا الحساب لا يملك تعديل محتوى الموقع"})
        patch = self._body()
        if not isinstance(patch, dict) or not patch:
            return self._json(400, {"error": "bad_request", "message": "لا يوجد تعديل"})
        try:
            merged = merge_content(patch)
        except OSError as e:
            return self._json(500, {"error": "write_failed", "message": str(e)})
        self._json(200, {"ok": True, "content": merged})


    def register(self):
        phone = verify_session(self.headers.get("Authorization"))
        if not phone:
            return self._json(401, {"error": "unauthorized",
                                    "message": "أكمل التحقّق من رقمك أولاً"})
        body = self._body()
        name = str(body.get("name", "")).strip()[:80]
        role = str(body.get("role", "customer")).strip()
        if len(name) < 2:
            return self._json(400, {"error": "bad_name", "message": "اكتب اسمك الكامل"})
        if role not in ("customer", "driver"):
            return self._json(400, {"error": "bad_role", "message": "اختر نوع الحساب"})

        record = {"phone": phone, "name": name, "role": role,
                  "createdAt": int(time.time()), "source": "web", "verified": True}
        fresh = append_record(ACCOUNTS_FILE, record, "phone")
        self._json(200, {"ok": True, "new": fresh, "phone": phone, "role": role})


    def web_signup(self):
        body = self._body()
        phone = normalize(str(body.get("phone", "")))
        name = str(body.get("name", "")).strip()[:80]
        role = str(body.get("role", "customer")).strip()
        if not phone:
            return self._json(400, {"error": "bad_phone",
                                    "message": f"رقم عُماني من {NATIONAL_LEN} أرقام"})
        if len(name) < 2:
            return self._json(400, {"error": "bad_name", "message": "اكتب اسمك الكامل"})
        if role not in ("customer", "driver"):
            return self._json(400, {"error": "bad_role", "message": "اختر نوع الحساب"})

        record = {"phone": phone, "name": name, "role": role,
                  "createdAt": int(time.time()), "source": "web", "verified": False}
        fresh = append_record(ACCOUNTS_FILE, record, "phone")
        self._json(200, {"ok": True, "new": fresh, "phone": phone, "role": role})


    def notify_signup(self):
        body = self._body()
        phone = normalize(str(body.get("phone", "")))
        if not phone:
            return self._json(400, {"error": "bad_phone",
                                    "message": f"رقم عُماني من {NATIONAL_LEN} أرقام"})
        fresh = append_record(NOTIFY_FILE, {"phone": phone, "at": int(time.time())}, "phone")
        self._json(200, {"ok": True, "new": fresh})


    WEB_TYPES = {".html": "text/html; charset=utf-8",
                 ".css": "text/css; charset=utf-8",
                 ".js": "application/javascript; charset=utf-8",
                 ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon",
                 ".webmanifest": "application/manifest+json"}

    def serve_web(self, rel: str):
        root = (pathlib.Path(__file__).parent / "web").resolve()
        target = (root / rel).resolve()


        if not str(target).startswith(str(root)) or not target.is_file():
            return self._json(404, {"error": "not_found"})
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",
                         self.WEB_TYPES.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")


        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)


    RULES = core.Rules()

    def _phone(self):
        return verify_session(self.headers.get("Authorization"))

    def _need_points(self, pickup, dropoff):
        for p in (pickup, dropoff):
            if not isinstance(p, dict):
                return "نقطة غير صالحة"
            try:
                p["lat"], p["lon"] = float(p["lat"]), float(p["lon"])
            except (KeyError, TypeError, ValueError):
                return "إحداثيات ناقصة"
            if not (16.0 <= p["lat"] <= 27.0 and 51.0 <= p["lon"] <= 60.5):
                return "الموقع خارج سلطنة عمان"
            p.setdefault("zone", core.nearest_zone(p["lat"], p["lon"]))
            p["name"] = str(p.get("name", ""))[:120]
        return None

    def api_me(self):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized"})
        data = store.get()
        c = store.customer_for(phone, data, create=False)
        d = store.driver_for(phone, data, create=False)
        acct = _read_json(ACCOUNTS_FILE, [])
        rec = next((r for r in acct if r.get("phone") == phone), None)
        self._json(200, {
            "phone": phone,
            "name": (rec or {}).get("name", ""),
            "isOwner": phone == OWNER_PHONE,
            "customer": None if not c else
                {k: c[k] for k in ("id", "name", "walletBalance", "points", "lifetimePoints")},
            "driver": None if not d else
                {k: d[k] for k in ("id", "name", "approval", "presence",
                                   "earningsBalance", "completedOrders")},
            "nextPayout": core.next_payout(self.RULES),
        })

    def _voucher_error(self, v: dict | None) -> dict | None:
        if v is None:
            return {"error": "bad_voucher", "message": "القسيمة غير موجودة"}
        if v.get("usedAt"):
            return {"error": "voucher_used", "message": "هذه القسيمة مستخدَمة من قبل"}
        if v.get("expiresAt") and time.time() > v["expiresAt"]:
            return {"error": "voucher_expired", "message": "انتهت صلاحية القسيمة"}
        return None

    def _voucher_for_quote(self, v: dict) -> dict:
        return {"kind": v["kind"], "value": v["value"],
                "maxDiscount": v["maxDiscount"], "rewardTitle": v["rewardTitle"]}

    def api_quote(self):
        b = self._body()
        pickup, dropoff = b.get("pickup"), b.get("dropoff")
        bad = self._need_points(pickup, dropoff)
        if bad:
            return self._json(400, {"error": "invalid_input", "message": bad})
        kind = b.get("kind", "errand")
        if kind not in core.KIND_MULTIPLIER:
            return self._json(400, {"error": "invalid_kind",
                                    "message": "نوع طلب غير معروف"})

        voucher = None
        voucher_id = str(b.get("voucherID") or "")
        if voucher_id:
            phone = self._phone()
            if not phone:
                return self._json(401, {"error": "unauthorized",
                                        "message": "سجّل الدخول أولاً"})
            data = store.get()
            c = store.customer_for(phone, data, create=False)
            v = c and store.find(data["vouchers"], id=voucher_id, ownerID=c["id"])
            err = self._voucher_error(v)
            if err:
                return self._json(400, err)
            voucher = self._voucher_for_quote(v)

        price = core.quote(pickup, dropoff, kind, self.RULES, voucher=voucher)
        self._json(200, price)

    def api_create_order(self):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized",
                                    "message": "سجّل الدخول أولاً"})
        b = self._body()
        pickup, dropoff = b.get("pickup"), b.get("dropoff")
        bad = self._need_points(pickup, dropoff)
        if bad:
            return self._json(400, {"error": "invalid_input", "message": bad})
        kind = b.get("kind", "errand")
        if kind not in core.KIND_MULTIPLIER:
            return self._json(400, {"error": "invalid_kind"})
        payment = b.get("payment", "cash")
        if payment not in ("cash", "wallet"):
            return self._json(400, {"error": "invalid_payment"})
        voucher_id = str(b.get("voucherID") or "")

        def build(data):
            c = store.customer_for(phone, data)
            if c.get("isBlocked"):
                return {"error": "blocked", "message": "هذا الحساب موقوف"}

            voucher_row, voucher_for_quote = None, None
            if voucher_id:
                voucher_row = store.find(data["vouchers"], id=voucher_id, ownerID=c["id"])
                err = self._voucher_error(voucher_row)
                if err:
                    return err
                voucher_for_quote = self._voucher_for_quote(voucher_row)

            price = core.quote(pickup, dropoff, kind, self.RULES, voucher=voucher_for_quote)

            if payment == "wallet" and c["walletBalance"] < price["total"]:
                return {"error": "insufficient_funds",
                        "message": "رصيد المحفظة لا يكفي — اختر الدفع نقداً"}
            order = {
                "id": store.new_id(), "ref": core.new_ref(),
                "customerID": c["id"], "customerPhone": phone,
                "driverID": None, "kind": kind, "status": "searching",
                "pickup": pickup, "dropoff": dropoff,
                "price": price, "payment": payment,

                "dispatchMode": "open" if b.get("dispatchMode") == "open" else "nearby",
                "note": str(b.get("note", ""))[:280],
                "recipientPhone": str(b.get("recipientPhone", ""))[:20],
                "declinedBy": [], "createdAt": time.time(),
            }

            if voucher_row:
                # لا تُصرف القسيمة إلا بعد نجاح إنشاء الطلب فعلياً — لو فشل
                # الطلب (رصيد لا يكفي مثلاً) تبقى القسيمة صالحة للاستخدام.
                voucher_row["usedAt"] = time.time()
                voucher_row["orderID"] = None  # يُملأ أدناه بعد توليد المعرّف
                order["voucherID"] = voucher_row["id"]

            picks = core.candidates(order, data["drivers"], self.RULES)
            order["offeredTo"] = [x["driverID"] for x in picks]
            data["orders"].insert(0, order)
            if voucher_row:
                voucher_row["orderID"] = order["id"]
            return {"order": store.public_order(order), "offered": len(picks)}

        out = store.mutate(build)
        if "error" in out:
            return self._respond(out)
        print(f"  ✓ طلب {out['order']['ref']} · {out['order']['price']['total']:.3f} ر.ع."
              f" · عُرض على {out['offered']} مندوب", flush=True)
        self._json(200, out)

    def api_list_orders(self):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized"})
        data = store.get()
        c = store.customer_for(phone, data, create=False)
        if not c:
            return self._json(200, {"orders": []})
        mine = [store.public_order(o) for o in data["orders"]
                if o.get("customerID") == c["id"]]
        self._json(200, {"orders": mine[:50]})


    def _driver(self, data):
        phone = self._phone()
        return (phone, store.driver_for(phone, data)) if phone else (None, None)

    def api_driver_presence(self):
        if not self._phone():
            return self._json(401, {"error": "unauthorized"})
        want = str(self._body().get("presence", "")).strip()
        if want not in ("online", "offline"):
            return self._json(400, {"error": "invalid_presence"})

        def run(data):
            phone, d = self._driver(data)
            if not d:
                return {"error": "unauthorized", "message": "سجّل الدخول"}
            if want == "online":
                if d["approval"] != "approved":
                    return {"error": "not_approved",
                            "message": "حسابك بانتظار موافقة الإدارة"}
                if d["earningsBalance"] < self.RULES.min_earnings_balance:
                    return {"error": "balance",
                            "message": f"رصيدك {d['earningsBalance']:.3f} ر.ع. — "
                                       f"عبّئ محفظتك لتستلم طلبات"}
            d["presence"] = want
            return {"ok": True, "presence": want}

        out = store.mutate(run)
        self._respond(out)

    def api_driver_feed(self):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized"})
        data = store.get()
        d = store.driver_for(phone, data, create=False)
        if not d:
            return self._json(200, {"driver": None, "active": None, "offers": []})

        active = next((o for o in data["orders"]
                       if o.get("driverID") == d["id"]
                       and o["status"] in ("accepted", "pickedUp")), None)
        offers, board = [], []
        if not active and core.eligible(d, self.RULES):
            for o in data["orders"]:
                if o["status"] != "searching" or o.get("driverID"):
                    continue
                if d["id"] in o.get("declinedBy", []):
                    continue
                picks = core.candidates(o, [d], self.RULES)
                if picks:
                    row = store.public_order(o, "customer")
                    row["km"] = picks[0]["km"]
                    row["onPath"] = picks[0].get("onPath", False)
                    offers.append(row)


            near = {r["id"] for r in offers}
            for row in core.board_for(d, data["orders"], self.RULES):
                if row["order"]["id"] in near:
                    continue
                item = store.public_order(row["order"], "customer")
                item["km"] = row["km"]
                item["onPath"] = row["onPath"]
                board.append(item)

        self._json(200, {
            "driver": {k: d[k] for k in ("id", "name", "presence", "approval",
                                         "earningsBalance", "completedOrders")},
            "active": store.public_order(active, "driver") if active else None,
            "offers": offers[:10],
            "board": board[:15],
            "destination": d.get("destination"),
            "acceptRadiusKm": d.get("acceptRadiusKm", self.RULES.accept_radius_km),
            "tier": self._tier(d),
            "floor": self.RULES.min_earnings_balance,
        })

    def api_driver_accept(self):
        oid = str(self._body().get("orderID", ""))

        def run(data):
            phone, d = self._driver(data)
            if not d:
                return {"error": "unauthorized", "message": "سجّل الدخول"}
            if d["approval"] != "approved":
                return {"error": "not_approved", "message": "حسابك غير معتمد"}
            if d["earningsBalance"] < self.RULES.min_earnings_balance:
                return {"error": "balance", "message": "رصيدك تحت الحدّ — عبّئ محفظتك"}
            o = store.find(data["orders"], id=oid)
            if not o:
                return {"error": "not_found", "message": "الطلب غير موجود"}
            if o.get("driverID"):
                return {"error": "taken", "message": "سبقك مندوب آخر لهذا الطلب"}
            if o["status"] != "searching":
                return {"error": "closed", "message": "الطلب لم يعد متاحاً"}
            o["driverID"] = d["id"]
            o["status"] = "accepted"
            o["acceptedAt"] = time.time()
            return {"ok": True, "order": store.public_order(o, "driver")}

        out = store.mutate(run)
        if "ok" not in out:
            return self._respond(out)
        print(f"  ✓ قُبل {out['order']['ref']}", flush=True)
        self._json(200, out)

    def api_driver_advance(self):
        oid = str(self._body().get("orderID", ""))
        NEXT = {"accepted": "pickedUp", "pickedUp": "delivered"}

        def run(data):
            phone, d = self._driver(data)
            if not d:
                return {"error": "unauthorized", "message": "سجّل الدخول"}
            o = store.find(data["orders"], id=oid)
            if not o or o.get("driverID") != d["id"]:
                return {"error": "not_yours", "message": "هذا الطلب ليس لك"}
            nxt = NEXT.get(o["status"])
            if not nxt:
                return {"error": "final", "message": "الطلب منتهٍ"}

            o["status"] = nxt
            note = None
            if nxt == "delivered":
                o["deliveredAt"] = time.time()
                commission = o["price"]["commission"]
                payout = o["price"]["driverPayout"]
                if o["payment"] == "cash":

                    d["earningsBalance"] = round(d["earningsBalance"] - commission, 3)
                else:
                    d["earningsBalance"] = round(d["earningsBalance"] + payout, 3)
                d["completedOrders"] += 1

                c = store.find(data["customers"], id=o["customerID"])
                if c:
                    earned = 10 + int(o["price"]["total"] * 10)
                    c["points"] += earned
                    c["lifetimePoints"] += earned
                    if o["payment"] == "wallet":
                        c["walletBalance"] = round(
                            max(0.0, c["walletBalance"] - o["price"]["total"]), 3)
                    o["pointsEarned"] = earned

                if d["earningsBalance"] < self.RULES.min_earnings_balance:
                    d["presence"] = "offline"
                    note = (f"رصيدك {d['earningsBalance']:.3f} ر.ع. — "
                            f"عبّئ محفظتك لتعود للعمل")
            return {"ok": True, "status": nxt,
                    "balance": d["earningsBalance"], "note": note}

        out = store.mutate(run)
        self._respond(out)


    TIERS = [
        (0,    "برونزي",  0.000),
        (60,   "فضي",     0.010),
        (200,  "ذهبي",    0.025),
        (500,  "بلاتيني", 0.040),
    ]

    def _tier(self, d: dict) -> dict:
        pts = max(0, d.get("tierPoints", 0))
        idx = 0
        for i, (need, _, _) in enumerate(self.TIERS):
            if pts >= need:
                idx = i
        need, title, discount = self.TIERS[idx]
        nxt = self.TIERS[idx + 1] if idx + 1 < len(self.TIERS) else None
        rating = (round(d["ratingSum"] / d["ratingCount"], 1)
                  if d.get("ratingCount") else None)
        return {
            "title": title, "points": pts,
            "commission": round(max(self.RULES.min_commission,
                                    self.RULES.fixed_commission * (1 - discount)), 3),
            "nextTitle": nxt[1] if nxt else None,
            "nextAt": nxt[0] if nxt else None,
            "rating": rating,
        }


    def api_driver_destination(self):
        b = self._body()
        clear = bool(b.get("clear"))
        dest = None
        if not clear:
            idx = b.get("placeIndex")
            try:
                dest = core.PLACES[int(idx)]
            except (TypeError, ValueError, IndexError):
                return self._json(400, {"error": "bad_place",
                                        "message": "اختر وجهة من القائمة"})
        radius = b.get("acceptRadiusKm")

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized"}
            d = store.driver_for(phone, data, create=False)
            if not d:
                return {"error": "not_found", "message": "لا حساب مندوب"}
            if dest:
                d["destination"] = {"name": dest["name"], "zone": dest["zone"],
                                    "lat": dest["lat"], "lon": dest["lon"]}
            elif clear:
                d["destination"] = None
            if radius is not None:
                try:
                    d["acceptRadiusKm"] = min(12.0, max(1.0, float(radius)))
                except (TypeError, ValueError):
                    return {"error": "bad_radius", "message": "نطاق غير صالح"}
            return {"ok": True, "destination": d.get("destination"),
                    "acceptRadiusKm": d.get("acceptRadiusKm")}

        out = store.mutate(run)
        self._respond(out)

    def api_driver_decline(self):
        oid = str(self._body().get("orderID", ""))

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized"}
            d = store.driver_for(phone, data, create=False)
            o = store.find(data["orders"], id=oid)
            if not d or not o:
                return {"error": "not_found"}
            o.setdefault("declinedBy", [])
            if d["id"] not in o["declinedBy"]:
                o["declinedBy"].append(d["id"])
            return {"ok": True}

        out = store.mutate(run)
        self._respond(out)


    def _owner(self):
        phone = self._phone()
        return phone if phone == OWNER_PHONE else None

    def api_admin_overview(self):
        if not self._owner():
            return self._json(403, {"error": "forbidden",
                                    "message": "هذه اللوحة للمالك فقط"})
        data = store.get()
        today = time.time() - 86400
        orders = data["orders"]
        live = [o for o in orders if o["status"] in ("searching", "accepted", "pickedUp")]
        done = [o for o in orders if o["status"] == "delivered"]
        today_done = [o for o in done if o.get("deliveredAt", 0) >= today]

        self._json(200, {
            "stats": {
                "ordersToday": len([o for o in orders if o["createdAt"] >= today]),
                "revenueToday": round(sum(o["price"]["total"] for o in today_done), 3),
                "commissionToday": round(sum(o["price"]["commission"] for o in today_done), 3),
                "live": len(live),
                "drivers": len(data["drivers"]),
                "online": len([d for d in data["drivers"] if d["presence"] == "online"]),
                "pending": len([d for d in data["drivers"] if d["approval"] == "pending"]),
                "customers": len(data["customers"]),
            },
            "live": [store.public_order(o, "admin") for o in live[:20]],
            "drivers": [{k: d.get(k) for k in
                         ("id", "name", "phone", "zone", "approval", "presence",
                          "earningsBalance", "completedOrders")}
                        for d in data["drivers"]],
            "rules": {
                "baseFare": self.RULES.base_fare,
                "perKm": self.RULES.per_km,
                "perMinute": self.RULES.per_minute,
                "minimumFare": self.RULES.minimum_fare,
                "fixedCommission": self.RULES.fixed_commission,
                "minEarningsBalance": self.RULES.min_earnings_balance,
            },
            "nextPayout": core.next_payout(self.RULES),
        })

    def api_admin_approve(self):
        if not self._owner():
            return self._json(403, {"error": "forbidden"})
        b = self._body()
        did = str(b.get("driverID", ""))
        state = str(b.get("approval", ""))
        if state not in ("approved", "rejected", "suspended", "pending"):
            return self._json(400, {"error": "bad_state"})

        def run(data):
            d = store.find(data["drivers"], id=did)
            if not d:
                return {"error": "not_found", "message": "المندوب غير موجود"}
            d["approval"] = state
            d["rejectionNote"] = str(b.get("note", ""))[:200] or None


            if state != "approved":
                d["presence"] = "offline"
            return {"ok": True, "approval": state, "name": d["name"]}

        out = store.mutate(run)
        self._respond(out)

    def api_admin_rules(self):
        if not self._owner():
            return self._json(403, {"error": "forbidden"})
        b = self._body()
        LIMITS = {
            "baseFare": (0.1, 5.0, "base_fare"),
            "perKm": (0.01, 2.0, "per_km"),
            "perMinute": (0.0, 0.5, "per_minute"),
            "minimumFare": (0.1, 10.0, "minimum_fare"),
            "fixedCommission": (0.0, 2.0, "fixed_commission"),
            "minEarningsBalance": (-20.0, 0.0, "min_earnings_balance"),
        }
        changed = {}
        for key, (lo, hi, attr) in LIMITS.items():
            if key not in b:
                continue
            try:
                v = float(b[key])
            except (TypeError, ValueError):
                return self._json(400, {"error": "bad_value",
                                        "message": f"قيمة غير رقمية: {key}"})
            if not (lo <= v <= hi):
                return self._json(400, {"error": "out_of_range",
                                        "message": f"{key} يجب أن يكون بين {lo} و {hi}"})
            setattr(self.RULES, attr, round(v, 3))
            changed[key] = round(v, 3)
        print(f"  ⚙ عُدّلت القواعد: {changed}", flush=True)
        self._json(200, {"ok": True, "changed": changed})


    REWARDS = [
        {"id": "p10",  "title": "خصم ١٠٪",      "detail": "حتى ٥٠٠ بيسة",
         "cost": 100, "kind": "percentOff", "value": 0.10, "maxDiscount": 0.500},
        {"id": "f500", "title": "خصم ٥٠٠ بيسة", "detail": "مبلغ ثابت على أي طلب",
         "cost": 150, "kind": "fixedOff",   "value": 0.500, "maxDiscount": 0.500},
        {"id": "cr1",  "title": "رصيد ١ ر.ع.",   "detail": "يُضاف لمحفظتك",
         "cost": 350, "kind": "walletCredit", "value": 1.000, "maxDiscount": 1.000},
        {"id": "free", "title": "توصيل مجاني",   "detail": "طلب واحد بلا رسوم",
         "cost": 400, "kind": "freeDelivery", "value": 1, "maxDiscount": 3.000},
        {"id": "p25",  "title": "خصم ٢٥٪",      "detail": "حتى ١ ر.ع.",
         "cost": 500, "kind": "percentOff", "value": 0.25, "maxDiscount": 1.000},
    ]

    def api_wallet(self):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized"})
        data = store.get()
        c = store.customer_for(phone, data, create=False)
        now = time.time()
        mine = [v for v in data["vouchers"] if c and v.get("ownerID") == c["id"]]
        active = [v for v in mine if not v.get("usedAt")
                  and not (v.get("expiresAt") and now > v["expiresAt"])]
        self._json(200, {
            "balance": c["walletBalance"] if c else 0.0,
            "points": c["points"] if c else 0,
            "lifetimePoints": c["lifetimePoints"] if c else 0,
            "rewards": self.REWARDS,
            "vouchers": active,
        })

    def api_wallet_topup(self):
        if not self._owner():
            return self._json(403, {"error": "forbidden",
                                    "message": "التعبئة تجري من الإدارة بعد استلام التحويل"})
        b = self._body()
        try:
            amount = round(float(b.get("amount", 0)), 3)
        except (TypeError, ValueError):
            return self._json(400, {"error": "bad_amount"})
        if not (0 < amount <= 100):
            return self._json(400, {"error": "bad_amount",
                                    "message": "المبلغ بين ٠ و ١٠٠ ر.ع."})
        who = normalize(str(b.get("phone", "")))
        if not who:
            return self._json(400, {"error": "bad_phone"})

        def run(data):
            c = store.customer_for(who, data, create=False)
            d = store.driver_for(who, data, create=False)
            if not c and not d:
                return {"error": "not_found", "message": "لا حساب بهذا الرقم"}
            if d:
                d["earningsBalance"] = round(d["earningsBalance"] + amount, 3)
            if c:
                c["walletBalance"] = round(c["walletBalance"] + amount, 3)
            data["transactions"].insert(0, {
                "id": store.new_id(), "phone": who, "kind": "topUp",
                "amount": amount, "at": time.time()})
            return {"ok": True, "amount": amount,
                    "wallet": c["walletBalance"] if c else None,
                    "earnings": d["earningsBalance"] if d else None}

        out = store.mutate(run)
        self._respond(out)

    def api_redeem(self):


        if not self._phone():
            return self._json(401, {"error": "unauthorized"})
        rid = str(self._body().get("rewardID", ""))
        reward = next((r for r in self.REWARDS if r["id"] == rid), None)
        if not reward:
            return self._json(400, {"error": "bad_reward"})

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized"}
            c = store.customer_for(phone, data)
            if c["points"] < reward["cost"]:
                return {"error": "not_enough",
                        "message": f"تحتاج {reward['cost'] - c['points']} نقطة إضافية"}
            c["points"] -= reward["cost"]
            if reward["kind"] == "walletCredit":
                c["walletBalance"] = round(c["walletBalance"] + reward["value"], 3)
                return {"ok": True, "kind": "credit", "title": reward["title"]}
            data["vouchers"].append({
                "id": store.new_id(), "ownerID": c["id"],
                "rewardTitle": reward["title"], "kind": reward["kind"],
                "value": reward["value"], "maxDiscount": reward["maxDiscount"],
                "issuedAt": time.time(), "usedAt": None, "orderID": None,
                "expiresAt": time.time() + 30 * 86400,
            })
            return {"ok": True, "kind": "voucher", "title": reward["title"]}

        out = store.mutate(run)
        self._respond(out)


    STATUS_STEPS = ["searching", "accepted", "pickedUp", "delivered"]

    def api_order_detail(self, oid: str):
        phone = self._phone()
        if not phone:
            return self._json(401, {"error": "unauthorized"})
        data = store.get()
        o = store.find(data["orders"], id=oid)
        if not o:
            return self._json(404, {"error": "not_found", "message": "الطلب غير موجود"})

        c = store.customer_for(phone, data, create=False)
        d = store.driver_for(phone, data, create=False)
        is_customer = c and o.get("customerID") == c["id"]
        is_driver = d and o.get("driverID") == d["id"]
        if not (is_customer or is_driver or phone == OWNER_PHONE):
            return self._json(403, {"error": "forbidden", "message": "هذا الطلب ليس لك"})

        view = "driver" if is_driver else ("admin" if phone == OWNER_PHONE else "customer")
        out = store.public_order(o, view)
        out["steps"] = self.STATUS_STEPS
        out["stepIndex"] = (self.STATUS_STEPS.index(o["status"])
                            if o["status"] in self.STATUS_STEPS else -1)
        out["rating"] = o.get("rating")
        out["pointsEarned"] = o.get("pointsEarned")


        drv = store.find(data["drivers"], id=o["driverID"]) if o.get("driverID") else None
        if drv and (is_customer or view == "admin"):
            rating = (round(drv["ratingSum"] / drv["ratingCount"], 1)
                      if drv.get("ratingCount") else None)
            out["driver"] = {
                "name": drv["name"], "phone": drv["phone"],
                "zone": drv["zone"], "rating": rating,
                "completedOrders": drv["completedOrders"],
            }
        self._json(200, out)

    def api_rate(self):
        b = self._body()
        oid = str(b.get("orderID", ""))
        try:
            stars = int(b.get("stars", 0))
        except (TypeError, ValueError):
            return self._json(400, {"error": "bad_rating"})
        if not (1 <= stars <= 5):
            return self._json(400, {"error": "bad_rating",
                                    "message": "التقييم من ١ إلى ٥"})

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized"}
            c = store.customer_for(phone, data, create=False)
            o = store.find(data["orders"], id=oid)
            if not o or not c or o.get("customerID") != c["id"]:
                return {"error": "not_yours", "message": "هذا الطلب ليس لك"}
            if o["status"] != "delivered":
                return {"error": "not_delivered", "message": "قيّم بعد التسليم"}
            if o.get("rating"):
                return {"error": "already", "message": "قيّمت هذا الطلب مسبقاً"}

            o["rating"] = stars
            o["ratedAt"] = time.time()
            d = store.find(data["drivers"], id=o.get("driverID"))
            if d:
                d["ratingSum"] = d.get("ratingSum", 0) + stars
                d["ratingCount"] = d.get("ratingCount", 0) + 1
                d["tierPoints"] = max(0, d.get("tierPoints", 0)
                                      + (3 if stars >= 4 else (-4 if stars <= 2 else 0)))
            return {"ok": True, "stars": stars,
                    "tierPoints": d["tierPoints"] if d else None}

        out = store.mutate(run)
        self._respond(out)

    def api_cancel(self):
        oid = str(self._body().get("orderID", ""))

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized"}
            c = store.customer_for(phone, data, create=False)
            o = store.find(data["orders"], id=oid)
            if not o or not c or o.get("customerID") != c["id"]:
                return {"error": "not_yours", "message": "هذا الطلب ليس لك"}
            if o["status"] != "searching":
                return {"error": "too_late",
                        "message": "قَبِل المندوب الطلب — تواصل مع الدعم "
                                   f"{SUPPORT_PHONE}"}
            o["status"] = "cancelled"
            o["cancelledAt"] = time.time()
            return {"ok": True}

        out = store.mutate(run)
        self._respond(out)


    DOC_LABELS = {
        "civilIDFront": "البطاقة المدنية — الوجه",
        "civilIDBack": "البطاقة المدنية — الظهر",
        "license": "رخصة القيادة",
        "ownership": "ملكية المركبة",
        "vehicle": "صور المركبة",
    }
    DOC_REQUIRED = ["civilIDFront", "license", "ownership", "vehicle"]

    def api_driver_register(self):
        b = self._body()
        name = str(b.get("name", "")).strip()[:80]
        # بلا قصّ قبل الفحص: التقصير يحوّل رقماً من تسع خانات إلى ثمانٍ
        # صالحة — أي رقم شخص آخر يُقبل بدل أن يُرفض.
        civil = re.sub(r"\D", "", str(b.get("civilID", "")))[:32]
        license_no = str(b.get("licenseNumber", "")).strip()[:30]
        zone = str(b.get("zone", "")).strip()
        vehicle = b.get("vehicle") or {}

        if len(name) < 3:
            return self._json(400, {"error": "bad_name", "message": "اكتب اسمك الكامل"})
        if len(civil) != CIVIL_ID_LEN:
            return self._json(400, {"error": "bad_civil",
                                    "message": f"الرقم المدني {CIVIL_ID_LEN} أرقام"})
        if zone not in core.ZONES:
            return self._json(400, {"error": "bad_zone", "message": "اختر منطقة عملك"})
        for key in ("make", "model", "plateNumber"):
            if not str(vehicle.get(key, "")).strip():
                return self._json(400, {"error": "bad_vehicle",
                                        "message": "أكمل بيانات المركبة"})

        def run(data):
            phone = self._phone()
            if not phone:
                return {"error": "unauthorized", "message": "سجّل الدخول أولاً"}


            taken = next((d for d in data["drivers"]
                          if d["docs"].get("civilID") == civil and d["phone"] != phone), None)
            if taken:
                return {"error": "civil_taken",
                        "message": f"هذا الرقم المدني مسجَّل لحساب «{taken['name']}». "
                                   "لا يمكن تسجيل أكثر من حساب برقم مدني واحد."}

            d = store.driver_for(phone, data)
            if d["approval"] == "approved":
                return {"error": "already_approved",
                        "message": "حسابك معتمد — للتعديل تواصل مع الدعم"}

            d["name"] = name
            d["zone"] = zone
            d["vehicle"] = {k: str(vehicle.get(k, ""))[:40]
                            for k in ("make", "model", "year", "plateNumber",
                                      "color", "ownershipNumber")}
            d["docs"]["civilID"] = civil
            d["docs"]["licenseNumber"] = license_no
            docs = b.get("documents") or {}
            assets = []
            for kind, ids in docs.items():
                if kind not in self.DOC_LABELS:
                    continue
                for fid in (ids if isinstance(ids, list) else [ids])[:4]:
                    assets.append({"kind": kind, "fileID": str(fid)[:64]})
            d["docs"]["assets"] = assets

            missing = [k for k in self.DOC_REQUIRED
                       if not [a for a in assets if a["kind"] == k]]
            if missing:
                return {"error": "missing_docs",
                        "message": "ناقص: " + "، ".join(self.DOC_LABELS[m] for m in missing)}

            d["approval"] = "pending"
            d["rejectionNote"] = None
            return {"ok": True, "approval": "pending", "assets": len(assets)}

        out = store.mutate(run)
        if "ok" not in out:
            return self._json(400, out)
        print(f"  ✓ طلب اعتماد مندوب · {out['assets']} مستند", flush=True)
        self._json(200, out)


    def upload_document(self):
        phone = verify_session(self.headers.get("Authorization"))
        if not phone:
            return self._json(401, {"error": "unauthorized",
                                    "message": "الجلسة غير صالحة، أعد تسجيل الدخول"})

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype or "boundary=" not in ctype:
            return self._json(400, {"error": "bad_request",
                                    "message": "صيغة الطلب غير صحيحة"})

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0


        if length <= 0 or length > MAX_UPLOAD + 8192:
            return self._json(413, {"error": "too_large",
                                    "message": "الملف أكبر من الحجم المسموح"})

        boundary = ctype.split("boundary=", 1)[1].strip().strip('"').encode()
        parsed = parse_multipart(self.rfile.read(length), boundary)
        blob = parsed["file"]
        if not blob:
            return self._json(400, {"error": "no_file", "message": "لا يوجد ملف"})
        if len(blob) > MAX_UPLOAD:
            return self._json(413, {"error": "too_large",
                                    "message": "الملف أكبر من الحجم المسموح"})

        sniffed = sniff_type(blob)
        if not sniffed:
            return self._json(415, {"error": "unsupported_type",
                                    "message": "صيغة الملف غير مدعومة"})
        real_mime, ext = sniffed


        declared = (parsed["declared_type"] or "").split(";")[0].strip()
        if declared and declared != real_mime:
            return self._json(415, {"error": "type_mismatch",
                                    "message": "نوع الملف لا يطابق محتواه"})

        kind = parsed["fields"].get("kind", "")
        if kind not in DOC_KINDS:
            return self._json(400, {"error": "bad_kind", "message": "نوع مستند غير معروف"})

        file_id = secrets.token_urlsafe(24)
        target = owner_dir(phone) / f"{kind}-{file_id}.{ext}"
        try:

            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
        except OSError as e:
            print(f"  ✗ تعذّر كتابة المستند: {e}", flush=True)
            return self._json(500, {"error": "write_failed",
                                    "message": "تعذّر حفظ الملف"})

        print(f"  ✓ مستند {kind} · {len(blob)} بايت · {file_id[:8]}…", flush=True)

        self._json(200, {"ok": True, "file_id": file_id,
                         "kind": kind, "content_type": real_mime,
                         "bytes": len(blob)})


    def request_otp(self):
        phone = normalize(self._body().get("phone", ""))
        if not phone:
            return self._json(400, {"error": "invalid_phone",
                                    "message": "رقم غير صالح"})

        ip = self.client_address[0]
        with _lock:
            if rate_limited(_ip_hits, ip, IP_LIMIT, IP_WINDOW):
                return self._json(429, {"error": "rate_limited",
                                        "message": "محاولات كثيرة، جرّب بعد قليل"})

            prev = _challenges.get(phone)
            if prev and time.time() - prev["sent"] < RESEND_COOLDOWN:
                wait = int(RESEND_COOLDOWN - (time.time() - prev["sent"]))
                return self._json(429, {"error": "cooldown", "retry_after": wait,
                                        "message": f"انتظر {wait} ثانية"})

            if rate_limited(_phone_hits, phone, PHONE_LIMIT, PHONE_WINDOW):
                return self._json(429, {"error": "rate_limited",
                                        "message": "تجاوزت عدد المحاولات لهذا الرقم"})

        code = "".join(secrets.choice("0123456789") for _ in range(CODE_LEN))
        ok, detail = send_whatsapp(phone, code)
        if not ok:

            print(f"  ✗ فشل الإرسال لـ +{phone}: {detail[:120]}", flush=True)


            return self._json(502, {"error": "send_failed",
                                    "message": "تعذّر إرسال الرمز، حاول مرة ثانية"})

        salt = secrets.token_bytes(16)
        with _lock:
            record_hit(_phone_hits, phone)
            record_hit(_ip_hits, ip)
            _challenges[phone] = {
                "salt": salt,
                "hash": hash_code(code, salt),
                "exp": time.time() + CODE_TTL,
                "attempts": 0,
                "sent": time.time(),
            }

        out = {"ok": True, "expires_in": CODE_TTL, "resend_after": RESEND_COOLDOWN}
        if DEV_MODE and ALLOW_DEV_CODE:
            out["dev_code"] = code
        self._json(200, out)

    def logout(self):
        phone = verify_session(self.headers.get("Authorization"))
        if not phone:

            return self._json(204, {})
        revoke_sessions(phone)
        self._json(200, {"ok": True})


    def verify_otp(self):
        body = self._body()
        phone = normalize(body.get("phone", ""))
        code = re.sub(r"\D", "", str(body.get("code", "")))
        if not phone or len(code) != CODE_LEN:
            return self._json(400, {"error": "invalid_input",
                                    "message": "بيانات غير مكتملة"})

        with _lock:
            ch = _challenges.get(phone)
            if not ch:
                return self._json(400, {"error": "no_challenge",
                                        "message": "اطلب رمزاً جديداً"})
            if time.time() > ch["exp"]:
                _challenges.pop(phone, None)
                return self._json(400, {"error": "expired",
                                        "message": "انتهت صلاحية الرمز"})
            if ch["attempts"] >= MAX_ATTEMPTS:
                _challenges.pop(phone, None)
                return self._json(429, {"error": "too_many_attempts",
                                        "message": "محاولات كثيرة، اطلب رمزاً جديداً"})

            ch["attempts"] += 1
            good = hmac.compare_digest(ch["hash"], hash_code(code, ch["salt"]))
            if not good:
                left = MAX_ATTEMPTS - ch["attempts"]
                return self._json(401, {"error": "wrong_code", "attempts_left": left,
                                        "message": "رمز غير صحيح"})
            _challenges.pop(phone, None)

        self._json(200, {"ok": True, "phone": phone, "token": issue_session(phone)})


def main():
    names = {
        "console": "وضع التطوير — الرمز يُطبع هنا ولا يُرسل",
        "whatsapp": f"واتساب Cloud API — القالب «{WA_TEMPLATE}» ({WA_LANG})",
        "twilio": f"SMS عبر Twilio من {TW_FROM}",
        "infobip": f"SMS عبر Infobip باسم {IB_FROM}",
        "unifonic": f"SMS عبر Unifonic باسم {UF_FROM}",
        "telegram": "Telegram",
    }
    banner = names.get(ACTIVE_PROVIDER, ACTIVE_PROVIDER)
    print("\n  خادم رمز التحقق — جاينك")
    print(f"  المنفذ: {PORT}")
    print(f"  الوضع: {banner}")
    if DEV_MODE:
        print("  (عيّن مزوّداً للإرسال الفعلي — راجع server/README.md)")
        if ALLOW_DEV_CODE:
            print("  ⚠ ALLOW_DEV_CODE=1 — الرمز يُعاد في رد الـHTTP.")
            print("    للتجربة المحلية فقط. لا ترفع الخادم بهذا الضبط.")
        else:
            print("  الرمز يُطبع هنا فقط. للتجربة: ALLOW_DEV_CODE=1 python3 otp_server.py")
    print(f"  الصحّة: http://localhost:{PORT}/health\n", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
