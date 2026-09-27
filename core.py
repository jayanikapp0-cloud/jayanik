
from __future__ import annotations

import math
import secrets
import time
from dataclasses import dataclass, field, asdict


CITY_CENTER = (23.5900, 58.4000)

PLACES = [
    {"name": "سوق مطرح", "zone": "مطرح", "lat": 23.6178, "lon": 58.5636, "details": "المدخل الرئيسي"},
    {"name": "جامع السلطان قابوس الأكبر", "zone": "بوشر", "lat": 23.5836, "lon": 58.3878, "details": ""},
    {"name": "سيتي سنتر مسقط", "zone": "السيب", "lat": 23.5866, "lon": 58.3411, "details": "البوابة ٣"},
    {"name": "أفنيوز مول مسقط", "zone": "بوشر", "lat": 23.5798, "lon": 58.3592, "details": "الدور الأرضي"},
    {"name": "مطار مسقط الدولي", "zone": "السيب", "lat": 23.5933, "lon": 58.2844, "details": "صالة الوصول"},
    {"name": "مستشفى خولة", "zone": "مطرح", "lat": 23.5946, "lon": 58.4295, "details": "مدخل الطوارئ"},
    {"name": "جامعة السلطان قابوس", "zone": "السيب", "lat": 23.5905, "lon": 58.1665, "details": "البوابة الرئيسية"},
    {"name": "شاطئ القرم", "zone": "مسقط", "lat": 23.6100, "lon": 58.4740, "details": ""},
    {"name": "روي - المنطقة التجارية", "zone": "مطرح", "lat": 23.6000, "lon": 58.5400, "details": "مبنى ١٢"},
    {"name": "الخوير - جمعية", "zone": "بوشر", "lat": 23.5900, "lon": 58.4200, "details": "قطعة ٤"},
    {"name": "العامرات - قطعة ٦", "zone": "العامرات", "lat": 23.5100, "lon": 58.5000, "details": "منزل ١٨، شارع ٣"},
    {"name": "المعبيلة الجنوبية", "zone": "السيب", "lat": 23.6300, "lon": 58.1900, "details": ""},
]

ZONES = ["مسقط", "مطرح", "بوشر", "السيب", "العامرات", "قريات"]


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def nearest_zone(lat: float, lon: float) -> str:
    best, best_d = "مسقط", float("inf")
    for p in PLACES:
        d = km_between(lat, lon, p["lat"], p["lon"])
        if d < best_d:
            best, best_d = p["zone"], d
    return best


def minutes_for_km(km: float) -> int:
    return max(4, int(round(km / 22.0 * 60)))


@dataclass
class Rules:
    base_fare: float = 0.800
    per_km: float = 0.120
    per_minute: float = 0.015
    minimum_fare: float = 1.000
    peak_multiplier: float = 1.25
    peak_hours: tuple = (7, 8, 13, 14, 17, 18, 19)


    fixed_commission: float = 0.160
    min_commission: float = 0.100

    min_earnings_balance: float = -1.500
    earnings_warn_balance: float = -0.750
    payout_weekdays: tuple = (6, 2)

    accept_radius_km: float = 3.0
    dispatch_fanout: int = 3
    promote_to_open_after: int = 60

    # كل قيمة هنا "كم تُخصَم من مسافة السائق الفعلية" — كلما زاد الخصم
    # صار السائق يبدو أقرب في الترتيب رغم بعده الحقيقي بالكيلومتر.
    on_path_bonus_km: float = 2.0
    same_zone_bonus_km: float = 0.5
    rating_km_per_star: float = 0.4      # لكل نجمة فوق المتوسط (٣)
    tier_bonus_km: float = 0.3            # للمندوب الأعلى نقاط ولاء (بلاتيني)
    tier_points_for_max_bonus: int = 500
    new_driver_rating: float = 3.0        # تقييم افتراضي محايد لمن لا تقييم له بعد

    zone_surcharge: dict = field(default_factory=lambda: {
        "مسقط": 0.0, "مطرح": 0.0, "بوشر": 0.0,
        "السيب": 0.100, "العامرات": 0.200, "قريات": 0.400,
    })


KIND_MULTIPLIER = {"errand": 1.0, "parcel": 1.10, "documents": 0.95}
KIND_TITLE = {"errand": "مشوار", "parcel": "طرد", "documents": "مستندات"}


def r3(v: float) -> float:
    return round(v + 1e-9, 3)


def quote(pickup: dict, dropoff: dict, kind: str, rules: Rules,
          at: float | None = None, voucher: dict | None = None) -> dict:
    km = round(km_between(pickup["lat"], pickup["lon"],
                          dropoff["lat"], dropoff["lon"]) * 10) / 10
    minutes = minutes_for_km(km)

    base = rules.base_fare
    distance = km * rules.per_km
    time_cost = minutes * rules.per_minute

    surcharge = max(rules.zone_surcharge.get(pickup.get("zone", ""), 0.0),
                    rules.zone_surcharge.get(dropoff.get("zone", ""), 0.0))

    pre_kind = base + distance + time_cost + surcharge
    kind_adj = pre_kind * (KIND_MULTIPLIER.get(kind, 1.0) - 1)

    hour = time.localtime(at or time.time()).tm_hour
    surge = (pre_kind + kind_adj) * (rules.peak_multiplier - 1)        if hour in rules.peak_hours else 0.0

    subtotal = max(pre_kind + kind_adj + surge, rules.minimum_fare)

    discount, voucher_title = 0.0, None
    if voucher:
        if voucher.get("kind") == "percentOff":
            discount = min(subtotal * voucher.get("value", 0),
                           voucher.get("maxDiscount", 0))
        elif voucher.get("kind") == "fixedOff":
            discount = min(voucher.get("value", 0), subtotal)
        elif voucher.get("kind") == "freeDelivery":
            discount = min(base + distance, voucher.get("maxDiscount", 0))
        voucher_title = voucher.get("rewardTitle")

    total = r3(max(0.0, subtotal - discount))
    commission = r3(min(total, max(rules.min_commission, rules.fixed_commission)))

    return {
        "km": km, "minutes": minutes,
        "base": r3(base), "distance": r3(distance), "time": r3(time_cost),
        "zoneSurcharge": r3(surcharge), "kindAdjustment": r3(kind_adj),
        "surge": r3(surge), "discount": r3(discount),
        "voucherTitle": voucher_title,
        "total": total,
        "commission": commission,
        "driverPayout": r3(total - commission),
        "isPeak": hour in rules.peak_hours,
    }


def distance_to_path(plat: float, plon: float,
                     alat: float, alon: float,
                     blat: float, blon: float) -> float:
    km_lat = 111.0
    km_lon = 111.0 * math.cos(math.radians(alat))
    bx, by = (blon - alon) * km_lon, (blat - alat) * km_lat
    px, py = (plon - alon) * km_lon, (plat - alat) * km_lat
    seg = bx * bx + by * by
    if seg < 1e-9:
        return math.hypot(px, py)
    t = max(0.0, min(1.0, (px * bx + py * by) / seg))
    return math.hypot(px - bx * t, py - by * t)


def matches_destination(order: dict, driver: dict, corridor_km: float = 6.0) -> bool:
    dest = driver.get("destination")
    if not dest:
        return False

    pickup, dropoff = order["pickup"], order["dropoff"]
    off = distance_to_path(pickup["lat"], pickup["lon"],
                           driver["lat"], driver["lon"],
                           dest["lat"], dest["lon"])
    if off > corridor_km:
        return False

    from_pickup = km_between(pickup["lat"], pickup["lon"], dest["lat"], dest["lon"])
    from_dropoff = km_between(dropoff["lat"], dropoff["lon"], dest["lat"], dest["lon"])
    if from_dropoff >= from_pickup:
        return False

    off_drop = distance_to_path(dropoff["lat"], dropoff["lon"],
                                driver["lat"], driver["lon"],
                                dest["lat"], dest["lon"])
    return off_drop <= corridor_km * 1.5


def is_open_board(order: dict, rules: Rules, now: float | None = None) -> bool:
    if order.get("dispatchMode") == "open":
        return True
    age = (now or time.time()) - order.get("createdAt", 0)
    return age >= rules.promote_to_open_after


def eligible(d: dict, rules: Rules) -> bool:
    return (d.get("presence") == "online"
            and d.get("approval") == "approved"
            and d.get("earningsBalance", 0) >= rules.min_earnings_balance)


def board_for(driver: dict, orders: list[dict], rules: Rules,
              now: float | None = None) -> list[dict]:
    if not eligible(driver, rules):
        return []
    out = []
    for o in orders:
        if o["status"] != "searching" or o.get("driverID"):
            continue
        if driver["id"] in o.get("declinedBy", []):
            continue
        if not is_open_board(o, rules, now):
            continue
        km = round(km_between(driver["lat"], driver["lon"],
                              o["pickup"]["lat"], o["pickup"]["lon"]) * 10) / 10
        on_path = matches_destination(o, driver, driver.get("corridorKm", 6.0))
        out.append({"order": o, "km": km, "onPath": on_path})
    out.sort(key=lambda r: (not r["onPath"], r["km"]))
    return out


def driver_rating(d: dict) -> float:
    """Average rating, or a neutral default for drivers with no ratings yet
    (so a brand-new driver isn't scored as if they had zero stars)."""
    count = d.get("ratingCount", 0)
    if count:
        return d["ratingSum"] / count
    return None


def match_score(km: float, on_path: bool, same_zone: bool,
                rating: float | None, tier_points: int, rules: Rules) -> float:
    """Lower is better. An 'effective distance' in km: real distance minus
    bonuses for driver quality/fit, so a slightly-farther but stronger
    driver can outrank a slightly-closer weaker one."""
    score = km
    if on_path:
        score -= rules.on_path_bonus_km
    if same_zone:
        score -= rules.same_zone_bonus_km
    stars = rating if rating is not None else rules.new_driver_rating
    score -= (stars - rules.new_driver_rating) * rules.rating_km_per_star
    tier_frac = min(max(tier_points, 0), rules.tier_points_for_max_bonus)              / max(1, rules.tier_points_for_max_bonus)
    score -= tier_frac * rules.tier_bonus_km
    return score


def candidates(order: dict, drivers: list[dict], rules: Rules) -> list[dict]:
    zone = order["pickup"].get("zone", "")
    out = []
    for d in drivers:
        if not eligible(d, rules):
            continue
        if order["id"] in d.get("declined", []):
            continue
        km = round(km_between(d["lat"], d["lon"],
                              order["pickup"]["lat"], order["pickup"]["lon"]) * 10) / 10
        radius = min(max(1.0, d.get("acceptRadiusKm", rules.accept_radius_km)), 12.0)


        on_path = matches_destination(order, d, d.get("corridorKm", 6.0))            if order.get("dropoff") else False
        if km > radius and not on_path:
            continue

        rating = driver_rating(d)
        same_zone = d.get("zone") == zone
        on_path_flag = on_path and km > radius
        score = match_score(km, on_path_flag, same_zone, rating,
                            d.get("tierPoints", 0), rules)

        out.append({"driverID": d["id"], "name": d.get("name", ""),
                    "km": km, "sameZone": same_zone,
                    "onPath": on_path_flag,
                    "rating": round(rating, 2) if rating is not None else None,
                    "score": r3(score)})


    out.sort(key=lambda c: c["score"])
    return out[: rules.dispatch_fanout]


def next_payout(rules: Rules, now: float | None = None) -> str:
    now = now or time.time()
    for offset in range(1, 8):
        t = time.localtime(now + offset * 86400)
        if t.tm_wday in rules.payout_weekdays:
            return time.strftime("%Y-%m-%d", t)
    return ""


def new_ref() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "JN-" + "".join(secrets.choice(alphabet) for _ in range(5))
