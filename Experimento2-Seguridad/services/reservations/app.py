import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

APP_NAME = "reservations"
PORT = int(os.getenv("PORT", "8080"))
SQLITE_PATH = os.getenv("SQLITE_PATH", "/data/reservations.db")

app = Flask(__name__)
_db_lock = threading.Lock()

reservations_created_total = Counter("reservations_created_total", "Reservas creadas correctamente")
reservations_rejected_total = Counter(
    "reservations_rejected_total",
    "Solicitudes rechazadas por reservas",
    ["reason"],
)
last_reservation_created_unix_seconds = Gauge(
    "last_reservation_created_unix_seconds",
    "Momento de creación de la última reserva",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def db_conn():
    conn = sqlite3.connect(SQLITE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    os.makedirs(os.path.dirname(SQLITE_PATH), exist_ok=True)
    with db_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                hotel_id TEXT NOT NULL,
                room_type TEXT NOT NULL,
                check_in TEXT NOT NULL,
                check_out TEXT NOT NULL,
                guests INTEGER NOT NULL,
                currency TEXT NOT NULL,
                total_amount REAL NOT NULL,
                cancellation_policy_id TEXT NOT NULL,
                quote_id TEXT NOT NULL,
                quote_hash TEXT NOT NULL,
                client_ip TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@app.post("/reservations")
def create_reservation():
    if request.headers.get("X-Integrity-Verified") != "true":
        reservations_rejected_total.labels(reason="missing_verified_header").inc()
        return jsonify({"error": "missing_verified_header"}), 403

    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor") or {}
    reservation = payload.get("reservation") or {}
    meta = payload.get("meta") or {}

    request_id = request.headers.get("X-Request-ID") or meta.get("requestId")
    if not request_id or request_id != meta.get("requestId"):
        reservations_rejected_total.labels(reason="request_id_mismatch").inc()
        return jsonify({"error": "request_id_mismatch"}), 400

    reservation_id = str(uuid.uuid4())
    with _db_lock:
        try:
            with db_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO reservations (
                        reservation_id, request_id, user_id, role, hotel_id, room_type,
                        check_in, check_out, guests, currency, total_amount,
                        cancellation_policy_id, quote_id, quote_hash, client_ip, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        reservation_id,
                        request_id,
                        actor.get("userId"),
                        actor.get("role"),
                        reservation.get("hotelId"),
                        reservation.get("roomType"),
                        reservation.get("checkIn"),
                        reservation.get("checkOut"),
                        int(reservation.get("guests")),
                        reservation.get("currency"),
                        float(reservation.get("totalAmount")),
                        reservation.get("cancellationPolicyId"),
                        reservation.get("quoteId"),
                        reservation.get("quoteHash"),
                        request.headers.get("X-Client-IP"),
                        now_iso(),
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError:
            reservations_rejected_total.labels(reason="duplicate_request_id").inc()
            return jsonify({"error": "duplicate_request_id", "requestId": request_id}), 409

    reservations_created_total.inc()
    last_reservation_created_unix_seconds.set(datetime.now(timezone.utc).timestamp())
    return jsonify({"reservationId": reservation_id, "requestId": request_id, "status": "CREATED"}), 201


@app.get("/reservations/by-request/<request_id>")
def get_by_request_id(request_id: str):
    with db_conn() as conn:
        row = conn.execute(
            """
            SELECT reservation_id, request_id, user_id, role, hotel_id, room_type,
                   check_in, check_out, guests, currency, total_amount,
                   cancellation_policy_id, quote_id, quote_hash, client_ip, created_at
            FROM reservations
            WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
    if not row:
        return jsonify({"error": "reservation_not_found", "requestId": request_id}), 404
    return jsonify({
        "reservationId": row["reservation_id"],
        "requestId": row["request_id"],
        "userId": row["user_id"],
        "role": row["role"],
        "hotelId": row["hotel_id"],
        "roomType": row["room_type"],
        "checkIn": row["check_in"],
        "checkOut": row["check_out"],
        "guests": row["guests"],
        "currency": row["currency"],
        "totalAmount": row["total_amount"],
        "cancellationPolicyId": row["cancellation_policy_id"],
        "quoteId": row["quote_id"],
        "quoteHash": row["quote_hash"],
        "clientIp": row["client_ip"],
        "createdAt": row["created_at"],
    })


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=PORT)
