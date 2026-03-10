import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pika
import psycopg2
from flask import Flask, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


APP_NAME = "reservas"
PORT = int(os.getenv("PORT", "8080"))
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")
PG_HOST = os.getenv("PG_HOST", "postgres")
PG_PORT = int(os.getenv("PG_PORT", "5432"))
PG_DB = os.getenv("PG_DB", "d2b")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASS = os.getenv("PG_PASS", "admin")

app = Flask(__name__)

reservations_created_total = Counter("reservations_created_total", "Reservas creadas")
payment_events_total = Counter(
    "reservation_payment_events_total",
    "Eventos de pago recibidos",
    ["event_type"],
)
service_heartbeat_total = Counter(
    "service_heartbeat_responses_total",
    "Cantidad de pong emitidos por reservas",
)
last_event_ts = Gauge("reservas_last_event_unix_seconds", "Ultimo evento procesado por reservas")

_rabbit_lock = threading.Lock()
_rabbit_publish_channel = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pg_conn():
    return psycopg2.connect(
        host=PG_HOST,
        port=PG_PORT,
        dbname=PG_DB,
        user=PG_USER,
        password=PG_PASS,
    )


def init_db():
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS reservations (
                    reservation_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    amount NUMERIC(12,2) NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
                """
            )
        conn.commit()


def rabbit_connection():
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    params = pika.ConnectionParameters(host=RABBIT_HOST, credentials=credentials, heartbeat=30)
    return pika.BlockingConnection(params)


def setup_topology(channel):
    channel.exchange_declare(exchange="booking.events", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="payments.events", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="control.ping", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="control.pong", exchange_type="topic", durable=True)

    channel.queue_declare(queue="reservas.payments", durable=True)
    channel.queue_bind(
        exchange="payments.events",
        queue="reservas.payments",
        routing_key="payment.*",
    )

    channel.queue_declare(queue="reservas.monitor", durable=True)
    channel.queue_bind(
        exchange="control.ping",
        queue="reservas.monitor",
        routing_key="health.ping",
    )


def connect_publish_channel():
    global _rabbit_publish_channel
    while _rabbit_publish_channel is None:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            _rabbit_publish_channel = channel
            app.logger.info("Canal RabbitMQ de publicacion listo en reservas")
        except Exception as exc:
            app.logger.warning("Esperando RabbitMQ para publicar: %s", exc)
            time.sleep(2)


def publish(exchange, routing_key, payload):
    with _rabbit_lock:
        if _rabbit_publish_channel is None:
            connect_publish_channel()
        body = json.dumps(payload).encode("utf-8")
        _rabbit_publish_channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=body,
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=2,
            ),
        )


def update_reservation_status(reservation_id: str, new_status: str):
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE reservations
                SET status = %s, updated_at = NOW()
                WHERE reservation_id = %s
                """,
                (new_status, reservation_id),
            )
        conn.commit()


def on_payment_event(ch, _method, _properties, body):
    try:
        event = json.loads(body.decode("utf-8"))
        event_type = event.get("eventType", "unknown")
        reservation_id = event.get("reservationId")
        if not reservation_id:
            ch.basic_ack(delivery_tag=_method.delivery_tag)
            return
        if event_type == "PaymentSucceeded":
            update_reservation_status(reservation_id, "CONFIRMED")
            payment_events_total.labels(event_type="PaymentSucceeded").inc()
        elif event_type == "PaymentFailed":
            update_reservation_status(reservation_id, "PAYMENT_FAILED")
            payment_events_total.labels(event_type="PaymentFailed").inc()
        last_event_ts.set(time.time())
    finally:
        ch.basic_ack(delivery_tag=_method.delivery_tag)


def on_health_ping(ch, _method, _properties, body):
    try:
        ping = json.loads(body.decode("utf-8"))
        payload = {
            "eventType": "HealthPong",
            "service": APP_NAME,
            "pingId": ping.get("pingId"),
            "timestamp": now_iso(),
        }
        publish("control.pong", "health.pong", payload)
        service_heartbeat_total.inc()
    finally:
        ch.basic_ack(delivery_tag=_method.delivery_tag)


def consumer_worker():
    while True:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            channel.basic_qos(prefetch_count=20)
            channel.basic_consume(queue="reservas.payments", on_message_callback=on_payment_event)
            channel.basic_consume(queue="reservas.monitor", on_message_callback=on_health_ping)
            app.logger.info("Consumidor RabbitMQ de reservas activo")
            channel.start_consuming()
        except Exception as exc:
            app.logger.warning("Consumidor reservas reiniciando: %s", exc)
            time.sleep(2)


@app.post("/reservas")
def create_reservation():
    data = request.get_json(silent=True) or {}
    user_id = data.get("userId", "anon")
    amount = float(data.get("amount", 100.0))
    reservation_id = str(uuid.uuid4())

    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO reservations (reservation_id, user_id, amount, status)
                VALUES (%s, %s, %s, %s)
                """,
                (reservation_id, user_id, amount, "PENDING_PAYMENT"),
            )
        conn.commit()

    event = {
        "eventType": "PaymentRequested",
        "reservationId": reservation_id,
        "userId": user_id,
        "amount": amount,
        "correlationId": reservation_id,
        "timestamp": now_iso(),
    }
    publish("booking.events", "payment.requested", event)
    reservations_created_total.inc()
    last_event_ts.set(time.time())

    return (
        jsonify(
            {
                "reservationId": reservation_id,
                "status": "PENDING_PAYMENT",
                "correlationId": reservation_id,
            }
        ),
        202,
    )


@app.get("/reservas/<reservation_id>")
def get_reservation(reservation_id):
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT reservation_id, user_id, amount::text, status, created_at, updated_at
                FROM reservations
                WHERE reservation_id = %s
                """,
                (reservation_id,),
            )
            row = cur.fetchone()
    if not row:
        return jsonify({"error": "reservation not found"}), 404
    return jsonify(
        {
            "reservationId": row[0],
            "userId": row[1],
            "amount": row[2],
            "status": row[3],
            "createdAt": row[4].isoformat(),
            "updatedAt": row[5].isoformat(),
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def bootstrap():
    while True:
        try:
            init_db()
            break
        except Exception as exc:
            app.logger.warning("Esperando PostgreSQL: %s", exc)
            time.sleep(2)

    connect_publish_channel()
    thread = threading.Thread(target=consumer_worker, daemon=True)
    thread.start()


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=PORT)
