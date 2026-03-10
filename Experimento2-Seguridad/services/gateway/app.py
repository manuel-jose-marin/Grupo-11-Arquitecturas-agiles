import os
import uuid

import requests
from flask import Flask, Response, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

APP_NAME = "gateway"
PORT = int(os.getenv("PORT", "8080"))
VERIFIER_URL = os.getenv("VERIFIER_URL", "http://verifier:8080/verify")
RESERVATIONS_QUERY_URL = os.getenv(
    "RESERVATIONS_QUERY_URL",
    "http://reservations:8080/reservations/by-request",
)
REQUEST_TIMEOUT_SECS = float(os.getenv("REQUEST_TIMEOUT_SECS", "10"))

app = Flask(__name__)

gateway_requests_total = Counter(
    "gateway_requests_total",
    "Solicitudes recibidas por gateway",
    ["route", "outcome"],
)


def resolve_request_id(payload: dict) -> str:
    meta = payload.get("meta") or {}
    return request.headers.get("X-Request-ID") or meta.get("requestId") or str(uuid.uuid4())


@app.post("/api/v1/reservations")
def create_reservation():
    payload = request.get_json(silent=True) or {}
    request_id = resolve_request_id(payload)
    forwarded_headers = {
        "Content-Type": "application/json",
        "X-Request-ID": request_id,
        "X-Forwarded-For": request.headers.get("X-Forwarded-For") or request.remote_addr or "unknown",
    }

    try:
        upstream = requests.post(
            VERIFIER_URL,
            json=payload,
            headers=forwarded_headers,
            timeout=REQUEST_TIMEOUT_SECS,
        )
        gateway_requests_total.labels(route="create_reservation", outcome=str(upstream.status_code)).inc()
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json"),
            headers={"X-Request-ID": request_id},
        )
    except requests.RequestException as exc:
        gateway_requests_total.labels(route="create_reservation", outcome="502").inc()
        return jsonify({"requestId": request_id, "error": "verifier_unavailable", "detail": str(exc)}), 502


@app.get("/api/v1/reservations/by-request/<request_id>")
def get_reservation_by_request(request_id: str):
    try:
        upstream = requests.get(
            f"{RESERVATIONS_QUERY_URL}/{request_id}",
            headers={"X-Request-ID": request_id},
            timeout=REQUEST_TIMEOUT_SECS,
        )
        gateway_requests_total.labels(route="query_reservation", outcome=str(upstream.status_code)).inc()
        return Response(
            upstream.content,
            status=upstream.status_code,
            content_type=upstream.headers.get("Content-Type", "application/json"),
            headers={"X-Request-ID": request_id},
        )
    except requests.RequestException as exc:
        gateway_requests_total.labels(route="query_reservation", outcome="502").inc()
        return jsonify({"requestId": request_id, "error": "reservations_unavailable", "detail": str(exc)}), 502


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
