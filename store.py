
from __future__ import annotations

import json
import os
import pathlib
import secrets
import threading
import time

DATA_FILE = pathlib.Path(os.environ.get("DATA_FILE", "./jaynak-data.json")).resolve()

_lock = threading.RLock()

_EMPTY = {
    "customers": [],
    "drivers": [],
    "orders": [],
    "transactions": [],
    "vouchers": [],
    "admins": [],
    "rewards": [],
}


def _read() -> dict:
    if not DATA_FILE.exists():
        return json.loads(json.dumps(_EMPTY))
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):

        DATA_FILE.rename(DATA_FILE.with_suffix(f".corrupt.{int(time.time())}"))
        return json.loads(json.dumps(_EMPTY))
    for k, v in _EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def _write(data: dict) -> None:

    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(DATA_FILE)


def get() -> dict:
    with _lock:
        return _read()


def mutate(fn):
    with _lock:
        data = _read()
        result = fn(data)
        _write(data)
        return result


def new_id() -> str:
    return secrets.token_hex(12)


def find(items: list, **kw):
    for it in items:
        if all(it.get(k) == v for k, v in kw.items()):
            return it
    return None


def customer_for(phone: str, data: dict, create: bool = True) -> dict | None:
    c = find(data["customers"], phone=phone)
    if c or not create:
        return c
    c = {
        "id": new_id(), "phone": phone,
        "name": f"زبون {phone[-4:]}",
        "walletBalance": 0.0, "points": 0, "lifetimePoints": 0,
        "isBlocked": False, "createdAt": time.time(),
    }
    data["customers"].append(c)
    return c


def driver_for(phone: str, data: dict, create: bool = True) -> dict | None:
    d = find(data["drivers"], phone=phone)
    if d or not create:
        return d
    d = {
        "id": new_id(), "phone": phone,
        "name": f"مندوب {phone[-4:]}",
        "zone": "بوشر", "lat": 23.59, "lon": 58.40,
        "presence": "offline", "approval": "pending",
        "earningsBalance": 0.0, "completedOrders": 0,
        "ratingSum": 0, "ratingCount": 0,
        "acceptRadiusKm": 3.0, "declined": [],
        "docs": {"civilID": "", "licenseNumber": "", "assets": []},
        "createdAt": time.time(),
    }
    data["drivers"].append(d)
    return d


def public_order(o: dict, viewer: str = "customer") -> dict:
    out = {k: o[k] for k in
           ("id", "ref", "kind", "status", "pickup", "dropoff",
            "price", "payment", "note", "createdAt", "voucherID") if k in o}
    out["driverID"] = o.get("driverID")
    if viewer == "driver" and o.get("driverID"):
        out["recipientPhone"] = o.get("recipientPhone", "")
        out["customerPhone"] = o.get("customerPhone", "")
    elif viewer == "admin":
        out["customerPhone"] = o.get("customerPhone", "")
        out["recipientPhone"] = o.get("recipientPhone", "")
    return out
