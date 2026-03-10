import json
import os
import threading
import time
import uuid
from datetime import datetime, timezone

import pika
from flask import Flask, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


APP_NAME = "monitor"
PORT = int(os.getenv("PORT", "8080"))
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")
PING_INTERVAL = int(os.getenv("PING_INTERVAL_SECS", "10"))
DETECTION_WINDOW = int(os.getenv("DETECTION_WINDOW_SECS", "20"))
TRACKED_SERVICES = [svc.strip() for svc in os.getenv("TRACKED_SERVICES", "reservas,pagos").split(",")]

app = Flask(__name__)

pings_sent_total = Counter("monitor_pings_sent_total", "Pings enviados por monitor")
pongs_received_total = Counter(
    "monitor_pongs_received_total",
    "Pongs recibidos por monitor",
    ["service"],
)
degradation_alerts_total = Counter(
    "monitor_degradation_alerts_total",
    "Alertas de degradacion disparadas por monitor",
    ["service"],
)
service_up = Gauge("monitor_service_up", "Servicio reportado como disponible", ["service"])
last_seen_seconds = Gauge("monitor_service_last_seen_seconds", "Ultimo pong recibido", ["service"])

_last_pong_ts = {svc: 0.0 for svc in TRACKED_SERVICES}
_publish_lock = threading.Lock()
_publish_channel = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rabbit_connection():
    credentials = pika.PlainCredentials(RABBIT_USER, RABBIT_PASS)
    params = pika.ConnectionParameters(host=RABBIT_HOST, credentials=credentials, heartbeat=30)
    return pika.BlockingConnection(params)


def setup_topology(channel):
    channel.exchange_declare(exchange="control.ping", exchange_type="topic", durable=True)
    channel.exchange_declare(exchange="control.pong", exchange_type="topic", durable=True)

    channel.queue_declare(queue="monitor.pong", durable=True)
    channel.queue_bind(exchange="control.pong", queue="monitor.pong", routing_key="health.pong")


def connect_publish_channel():
    global _publish_channel
    while _publish_channel is None:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            _publish_channel = channel
            app.logger.info("Canal RabbitMQ de publicacion listo en monitor")
        except Exception as exc:
            app.logger.warning("Esperando RabbitMQ en monitor: %s", exc)
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


def on_health_pong(ch, method, _properties, body):
    try:
        event = json.loads(body.decode("utf-8"))
        service = event.get("service")
        if service in _last_pong_ts:
            now = time.time()
            _last_pong_ts[service] = now
            pongs_received_total.labels(service=service).inc()
            service_up.labels(service=service).set(1)
            last_seen_seconds.labels(service=service).set(now)
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


def pong_consumer_worker():
    while True:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            channel.basic_qos(prefetch_count=20)
            channel.basic_consume(queue="monitor.pong", on_message_callback=on_health_pong)
            app.logger.info("Consumidor de pong activo")
            channel.start_consuming()
        except Exception as exc:
            app.logger.warning("Consumidor monitor reiniciando: %s", exc)
            time.sleep(2)


def ping_worker():
    while True:
        ping_id = str(uuid.uuid4())
        payload = {
            "eventType": "HealthPing",
            "pingId": ping_id,
            "source": APP_NAME,
            "timestamp": now_iso(),
        }
        publish("control.ping", "health.ping", payload)
        pings_sent_total.inc()
        time.sleep(PING_INTERVAL)


def degrade_check_worker():
    while True:
        now = time.time()
        for service in TRACKED_SERVICES:
            last = _last_pong_ts.get(service, 0.0)
            if last <= 0:
                service_up.labels(service=service).set(0)
                continue
            lag = now - last
            if lag > DETECTION_WINDOW:
                service_up.labels(service=service).set(0)
                degradation_alerts_total.labels(service=service).inc()
            else:
                service_up.labels(service=service).set(1)
        time.sleep(5)


@app.get("/status")
def status():
    now = time.time()
    services = {}
    for service in TRACKED_SERVICES:
        last = _last_pong_ts.get(service, 0.0)
        lag = None if last <= 0 else round(now - last, 3)
        services[service] = {
            "lastPongLagSeconds": lag,
            "healthy": lag is not None and lag <= DETECTION_WINDOW,
        }
    return jsonify(
        {
            "service": APP_NAME,
            "pingIntervalSecs": PING_INTERVAL,
            "detectionWindowSecs": DETECTION_WINDOW,
            "services": services,
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def bootstrap():
    connect_publish_channel()
    threads = [
        threading.Thread(target=pong_consumer_worker, daemon=True),
        threading.Thread(target=ping_worker, daemon=True),
        threading.Thread(target=degrade_check_worker, daemon=True),
    ]
    for thread in threads:
        thread.start()


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=PORT)
