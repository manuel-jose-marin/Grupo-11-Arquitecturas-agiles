import json
import os
import threading
import time
from datetime import datetime, timezone

import pika
import pybreaker
import redis
import requests
from flask import Flask, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


APP_NAME = "pagos"
PORT = int(os.getenv("PORT", "8080"))
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")
REDIS_HOST = os.getenv("REDIS_HOST", "redis-server")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASS = os.getenv("REDIS_PASS", "admin")
PROVIDER_URL = os.getenv("PROVIDER_URL", "http://wiremock:8080/pay")
REQUEST_TIMEOUT_SECS = float(os.getenv("REQUEST_TIMEOUT_SECS", "2.0"))

# TTL controlado
QUEUE_TTL_MS = int(os.getenv("QUEUE_TTL_MS", "30000"))  # 30s
QUEUE_MAX_LEN = int(os.getenv("QUEUE_MAX_LEN", "10000"))

app = Flask(__name__)

payment_requested_total = Counter("payments_requested_total", "Solicitudes de pago (validadas) recibidas")
payment_success_total = Counter("payments_success_total", "Pagos exitosos")
payment_failed_total = Counter("payments_failed_total", "Pagos fallidos")
payment_dlq_total = Counter("payments_dlq_total", "Mensajes enviados a DLQ")
heartbeat_responses_total = Counter("payments_heartbeat_responses_total", "Pong emitidos por pagos")
provider_call_seconds = Gauge("payments_provider_call_seconds", "Duracion de ultima llamada al proveedor")

_redis = None
_publish_lock = threading.Lock()
_publish_channel = None

circuit_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=20)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rabbit_connection():
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    params = pika.ConnectionParameters(host=RABBIT_HOST, credentials=credentials, heartbeat=30)
    return pika.BlockingConnection(params)


def setup_topology(channel):
    channel.exchange_declare(exchange="booking.events", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="payments.events", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="control.ping", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="control.pong", exchange_type="topic", durable=True)

    channel.exchange_declare(exchange="payments.dlq", exchange_type="direct", durable=True)
    channel.queue_declare(queue="payments.dlq", durable=True)
    channel.queue_bind(exchange="payments.dlq", queue="payments.dlq", routing_key="payment.failed")

    # Cola principal: pagos SOLO procesa después de votación exchange (payment.validated)
    # Si pagos cae, TTL -> DLQ para backlog controlado.
    channel.queue_declare(
        queue="payments.validated",
        durable=True,
        arguments={
            "x-message-ttl": QUEUE_TTL_MS,
            "x-dead-letter-exchange": "payments.dlq",
            "x-dead-letter-routing-key": "payment.failed",
            "x-max-length": QUEUE_MAX_LEN,
        },
    )
    channel.queue_bind(
        exchange="booking.events",
        queue="payments.validated",
        routing_key="payment.validated",
    )

    channel.queue_declare(queue="payments.monitor", durable=True)
    channel.queue_bind(
        exchange="control.ping",
        queue="payments.monitor",
        routing_key="health.ping",
    )


def redis_client():
    global _redis
    while _redis is None:
        try:
            _redis = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                password=REDIS_PASS,
                decode_responses=True,
                socket_timeout=2,
            )
            _redis.ping()
            app.logger.info("Redis listo en pagos")
        except Exception as exc:
            app.logger.warning("Esperando Redis: %s", exc)
            _redis = None
            time.sleep(2)
    return _redis


def connect_publish_channel():
    global _publish_channel
    while _publish_channel is None:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            _publish_channel = channel
            app.logger.info("Canal RabbitMQ de publicacion listo en pagos")
        except Exception as exc:
            app.logger.warning("Esperando RabbitMQ para publicacion: %s", exc)
            time.sleep(2)


def publish(exchange, routing_key, payload):
    with _publish_lock:
        if _publish_channel is None:
            connect_publish_channel()
        _publish_channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(payload).encode("utf-8"),
            properties=pika.BasicProperties(content_type="application/json", delivery_mode=2),
        )


def call_provider(amount: float):
    @circuit_breaker
    def _inner():
        start = time.time()
        response = requests.post(PROVIDER_URL, json={"amount": amount}, timeout=REQUEST_TIMEOUT_SECS)
        provider_call_seconds.set(time.time() - start)
        if response.status_code >= 400:
            raise RuntimeError(f"Provider status {response.status_code}")
        return response.json()

    return _inner()


def process_payment(event):
    reservation_id = event["reservationId"]
    amount = float(event.get("amount", 0))
    correlation_id = event.get("correlationId", reservation_id)

    cache = redis_client()
    if not cache.set(name=f"payments:processed:{reservation_id}", value="1", nx=True, ex=3600):
        app.logger.info("Reserva %s ya procesada; idempotencia aplicada", reservation_id)
        return

    publish(
        "payments.events",
        "payment.started",
        {
            "eventType": "PaymentProcessingStarted",
            "reservationId": reservation_id,
            "correlationId": correlation_id,
            "amount": amount,
            "timestamp": now_iso(),
        },
    )

    retries = [1, 2, 4]
    for idx, wait_secs in enumerate(retries, start=1):
        try:
            call_provider(amount)
            publish(
                "payments.events",
                "payment.succeeded",
                {
                    "eventType": "PaymentSucceeded",
                    "reservationId": reservation_id,
                    "correlationId": correlation_id,
                    "timestamp": now_iso(),
                },
            )
            payment_success_total.inc()
            return
        except Exception as exc:
            app.logger.warning("Intento %s fallo para %s: %s", idx, reservation_id, exc)
            if idx < len(retries):
                time.sleep(wait_secs)

    fail_event = {
        "eventType": "PaymentFailed",
        "reservationId": reservation_id,
        "correlationId": correlation_id,
        "reason": "provider_unavailable",
        "timestamp": now_iso(),
    }
    publish("payments.events", "payment.failed", fail_event)
    publish("payments.dlq", "payment.failed", fail_event)
    payment_failed_total.inc()
    payment_dlq_total.inc()


def on_payment_validated(ch, method, _properties, body):
    try:
        event = json.loads(body.decode("utf-8"))
        payment_requested_total.inc()
        process_payment(event)
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


def on_health_ping(ch, method, _properties, body):
    try:
        ping = json.loads(body.decode("utf-8"))
        pong = {
            "eventType": "HealthPong",
            "service": APP_NAME,
            "pingId": ping.get("pingId"),
            "timestamp": now_iso(),
        }
        publish("control.pong", "health.pong", pong)
        heartbeat_responses_total.inc()
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


def consumer_worker():
    while True:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            channel.basic_qos(prefetch_count=10)
            channel.basic_consume(queue="payments.validated", on_message_callback=on_payment_validated)
            channel.basic_consume(queue="payments.monitor", on_message_callback=on_health_ping)
            app.logger.info("Consumidor RabbitMQ de pagos activo")
            channel.start_consuming()
        except Exception as exc:
            app.logger.warning("Consumidor pagos reiniciando: %s", exc)
            time.sleep(2)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def bootstrap():
    redis_client()
    connect_publish_channel()
    thread = threading.Thread(target=consumer_worker, daemon=True)
    thread.start()


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=PORT)
