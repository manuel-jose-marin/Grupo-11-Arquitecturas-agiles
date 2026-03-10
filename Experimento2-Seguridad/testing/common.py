import hashlib
import hmac
import json
import uuid
from datetime import datetime, timezone
from urllib import request as urllib_request

HMAC_SECRET = "super-secret-exp2-key"
GATEWAY_URL = "http://localhost:8080/api/v1/reservations"

CRITICAL_FIELDS = [
    "hotelId",
    "roomType",
    "checkIn",
    "checkOut",
    "guests",
    "currency",
    "totalAmount",
    "cancellationPolicyId",
    "quoteId",
]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_base_payload() -> dict:
    return {
        "actor": {"userId": "user-001", "role": "traveler"},
        "reservation": {
            "hotelId": "HTL-BOG-001",
            "roomType": "DELUXE",
            "checkIn": "2026-03-20",
            "checkOut": "2026-03-23",
            "guests": 2,
            "currency": "COP",
            "totalAmount": 850000,
            "cancellationPolicyId": "POL-FLEX-24H",
            "quoteId": "QUOTE-2026-0001",
        },
        "meta": {
            "requestId": str(uuid.uuid4()),
            "nonce": str(uuid.uuid4()),
            "timestamp": iso_now(),
            "sig": "",
        },
    }


def canonical_business_string(reservation: dict, actor: dict) -> str:
    parts = [str(actor.get("userId", "")), str(actor.get("role", ""))]
    for field in CRITICAL_FIELDS:
        parts.append(str(reservation.get(field, "")))
    return "|".join(parts)


def compute_quote_hash(reservation: dict, actor: dict) -> str:
    return hashlib.sha256(canonical_business_string(reservation, actor).encode("utf-8")).hexdigest()


def compute_sig(payload: dict) -> str:
    actor = payload["actor"]
    reservation = payload["reservation"]
    meta = payload["meta"]
    quote_hash = reservation["quoteHash"]
    canonical = "|".join(
        [
            meta["requestId"],
            meta["nonce"],
            meta["timestamp"],
            canonical_business_string(reservation, actor),
            quote_hash,
        ]
    )
    return hmac.new(HMAC_SECRET.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_payload(payload: dict) -> dict:
    payload["reservation"]["quoteHash"] = compute_quote_hash(payload["reservation"], payload["actor"])
    payload["meta"]["sig"] = compute_sig(payload)
    return payload


def send_payload(payload: dict):
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        GATEWAY_URL,
        data=data,
        headers={"Content-Type": "application/json", "X-Request-ID": payload["meta"]["requestId"]},
        method="POST",
    )
    try:
        with urllib_request.urlopen(req) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, body
    except urllib_request.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
