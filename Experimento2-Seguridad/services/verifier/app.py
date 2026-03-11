import hashlib
import hmac
import os
import threading
import time
from datetime import datetime, timezone

import requests
from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

APP_NAME = "verifier"
PORT = int(os.getenv("PORT", "8080"))
RESERVATIONS_URL = os.getenv("RESERVATIONS_URL", "http://reservations:8080/reservations")
AUDIT_LOG_URL = os.getenv("AUDIT_LOG_URL", "http://auditlog:8080/events")
REQUEST_TIMEOUT_SECS = float(os.getenv("REQUEST_TIMEOUT_SECS", "10"))
RESERVATION_HMAC_SECRET = os.getenv("RESERVATION_HMAC_SECRET", "super-secret-exp2-key")
NONCE_TTL_SECONDS = int(os.getenv("NONCE_TTL_SECONDS", "120"))
ALLOWED_CLOCK_SKEW_SECONDS = int(os.getenv("ALLOWED_CLOCK_SKEW_SECONDS", "120"))

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

app = Flask(__name__)

verifier_requests_total = Counter(
    "verifier_requests_total",
    "Solicitudes procesadas por el verificador",
    ["outcome"],
)
verifier_integrity_violation_total = Counter(
    "verifier_integrity_violation_total",
    "Violaciones de integridad detectadas",
    ["reason"],
)
verifier_replay_detected_total = Counter(
    "verifier_replay_detected_total",
    "Replays detectados",
    ["reason"],
)
verifier_forwarded_total = Counter(
    "verifier_forwarded_total",
    "Solicitudes enviadas a reservations",
    ["outcome"],
)
verifier_audit_failures_total = Counter(
    "verifier_audit_failures_total",
    "Fallos al registrar auditoria",
    ["event_type"],
)
active_nonces_gauge = Gauge(
    "verifier_active_nonces",
    "Nonces activos en memoria dentro de la ventana TTL",
)

_nonce_lock = threading.Lock()
_nonce_registry = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(raw_value: str) -> datetime:
    normalized = raw_value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def prune_nonces(now_ts: float | None = None):
    current = now_ts or time.time()
    expired = [nonce for nonce, expires_at in _nonce_registry.items() if expires_at <= current]
    for nonce in expired:
        _nonce_registry.pop(nonce, None)
    active_nonces_gauge.set(len(_nonce_registry))


def reserve_nonce(nonce: str) -> bool:
    with _nonce_lock:
        current = time.time()
        prune_nonces(current)
        if nonce in _nonce_registry:
            return False
        _nonce_registry[nonce] = current + NONCE_TTL_SECONDS
        active_nonces_gauge.set(len(_nonce_registry))
        return True


def required_value(container: dict, key: str):
    value = container.get(key)
    if value is None:
        raise ValueError(f"missing_field:{key}")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"empty_field:{key}")
    return value


def canonical_business_string(reservation: dict, actor: dict) -> str:
    parts = [
        str(required_value(actor, "userId")),
        str(required_value(actor, "role")),
    ]
    for field in CRITICAL_FIELDS:
        parts.append(str(required_value(reservation, field)))
    return "|".join(parts)


def compute_quote_hash(reservation: dict, actor: dict) -> str:
    canonical = canonical_business_string(reservation, actor)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_sig(meta: dict, reservation: dict, actor: dict) -> str:
    quote_hash = required_value(reservation, "quoteHash")
    canonical = "|".join(
        [
            str(required_value(meta, "requestId")),
            str(required_value(meta, "nonce")),
            str(required_value(meta, "timestamp")),
            canonical_business_string(reservation, actor),
            str(quote_hash),
        ]
    )
    return hmac.new(
        RESERVATION_HMAC_SECRET.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def extract_payload_sections(payload: dict):
    actor = payload.get("actor")
    reservation = payload.get("reservation")
    meta = payload.get("meta")
    if not isinstance(actor, dict) or not isinstance(reservation, dict) or not isinstance(meta, dict):
        raise ValueError("invalid_payload_structure")
    return actor, reservation, meta


def client_ip() -> str:
    return request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown"


def build_audit_event(event_type: str, payload: dict, reason: str, request_id: str, ip_address: str) -> dict:
    actor = payload.get("actor") or {}
    reservation = payload.get("reservation") or {}
    meta = payload.get("meta") or {}
    return {
        "eventType": event_type,
        "requestId": request_id,
        "actor": {
            "userId": actor.get("userId"),
            "role": actor.get("role"),
        },
        "ip": ip_address,
        "timestamp": now_iso(),
        "requestTimestamp": meta.get("timestamp"),
        "nonce": meta.get("nonce"),
        "reason": reason,
        "quoteId": reservation.get("quoteId"),
        "payloadHash": reservation.get("quoteHash"),
    }


def write_audit_event(event: dict) -> bool:
    try:
        response = requests.post(AUDIT_LOG_URL, json=event, timeout=REQUEST_TIMEOUT_SECS)
        return response.status_code in (200, 201)
    except requests.RequestException:
        return False


def reject_with_audit(payload: dict, request_id: str, event_type: str, reason: str, status_code: int):
    event = build_audit_event(
        event_type=event_type,
        payload=payload,
        reason=reason,
        request_id=request_id,
        ip_address=client_ip(),
    )
    if not write_audit_event(event):
        verifier_audit_failures_total.labels(event_type=event_type).inc()
        verifier_requests_total.labels(outcome="audit_failed").inc()
        return jsonify({"requestId": request_id, "error": "audit_log_unavailable"}), 503
    return jsonify({"requestId": request_id, "error": event_type, "reason": reason}), status_code


@app.post("/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    request_id = request.headers.get("X-Request-ID") or ((payload.get("meta") or {}).get("requestId")) or "unknown"

    try:
        actor, reservation, meta = extract_payload_sections(payload)
        if request_id != meta.get("requestId"):
            raise ValueError("request_id_mismatch")
        expected_quote_hash = compute_quote_hash(reservation, actor)
        provided_quote_hash = required_value(reservation, "quoteHash")
        if not hmac.compare_digest(expected_quote_hash, str(provided_quote_hash)):
            verifier_requests_total.labels(outcome="integrity_violation").inc()
            verifier_integrity_violation_total.labels(reason="quote_hash_mismatch").inc()
            return reject_with_audit(payload, request_id, "INTEGRITY_VIOLATION", "quote_hash_mismatch", 422)

        expected_sig = compute_sig(meta, reservation, actor)
        provided_sig = required_value(meta, "sig")
        if not hmac.compare_digest(expected_sig, str(provided_sig)):
            verifier_requests_total.labels(outcome="integrity_violation").inc()
            verifier_integrity_violation_total.labels(reason="signature_mismatch").inc()
            return reject_with_audit(payload, request_id, "INTEGRITY_VIOLATION", "signature_mismatch", 422)

        request_ts = parse_timestamp(str(required_value(meta, "timestamp")))
        skew_seconds = abs((datetime.now(timezone.utc) - request_ts).total_seconds())
        if skew_seconds > ALLOWED_CLOCK_SKEW_SECONDS:
            verifier_requests_total.labels(outcome="replay_detected").inc()
            verifier_replay_detected_total.labels(reason="timestamp_out_of_window").inc()
            return reject_with_audit(payload, request_id, "REPLAY_DETECTED", "timestamp_out_of_window", 409)

        nonce = str(required_value(meta, "nonce"))
        if not reserve_nonce(nonce):
            verifier_requests_total.labels(outcome="replay_detected").inc()
            verifier_replay_detected_total.labels(reason="nonce_reused").inc()
            return reject_with_audit(payload, request_id, "REPLAY_DETECTED", "nonce_reused", 409)
    except ValueError as exc:
        verifier_requests_total.labels(outcome="integrity_violation").inc()
        verifier_integrity_violation_total.labels(reason=str(exc)).inc()
        return reject_with_audit(payload, request_id, "INTEGRITY_VIOLATION", str(exc), 422)

    forwarded_headers = {
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
        "X-Integrity-Verified": "true",
        "X-Client-IP": client_ip(),
    }
    try:
        upstream = requests.post(
            RESERVATIONS_URL,
            json=payload,
            headers=forwarded_headers,
            timeout=REQUEST_TIMEOUT_SECS,
        )
    except requests.RequestException as exc:
        verifier_forwarded_total.labels(outcome="502").inc()
        verifier_requests_total.labels(outcome="reservations_unavailable").inc()
        return jsonify(
            {
                "requestId": request_id,
                "error": "reservations_unavailable",
                "detail": str(exc),
            }
        ), 502

    verifier_forwarded_total.labels(outcome=str(upstream.status_code)).inc()
    verifier_requests_total.labels(outcome="accepted" if upstream.status_code < 400 else "forwarded_error").inc()

    if upstream.status_code < 400:
        accepted_event = build_audit_event(
            event_type="REQUEST_ACCEPTED",
            payload=payload,
            reason="verified_and_forwarded",
            request_id=request_id,
            ip_address=client_ip(),
        )
        if not write_audit_event(accepted_event):
            verifier_audit_failures_total.labels(event_type="REQUEST_ACCEPTED").inc()

    return Response(
        upstream.content,
        status=upstream.status_code,
        content_type=upstream.headers.get("Content-Type", "application/json"),
        headers={"X-Request-ID": request_id},
    )


@app.get("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "service": APP_NAME,
            "nonceTtlSeconds": NONCE_TTL_SECONDS,
            "allowedClockSkewSeconds": ALLOWED_CLOCK_SKEW_SECONDS,
        }
    )


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
