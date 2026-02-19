import json
import os
import threading
import time
from datetime import datetime, timezone

import pika
from flask import Flask, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest


APP_NAME = "validador"
PORT = int(os.getenv("PORT", "8080"))
RABBIT_HOST = os.getenv("RABBIT_HOST", "rabbitmq")
RABBIT_USER = os.getenv("RABBIT_USER", "guest")
RABBIT_PASS = os.getenv("RABBIT_PASS", "guest")

FAULTY_CALCULATOR = os.getenv("FAULTY_CALCULATOR", "calc_c")
FAULTY_DELTA = float(os.getenv("FAULTY_DELTA", "5.0"))

# TTL controlado
QUEUE_TTL_MS = int(os.getenv("QUEUE_TTL_MS", "30000"))  # 30s
QUEUE_MAX_LEN = int(os.getenv("QUEUE_MAX_LEN", "10000"))

app = Flask(__name__)

validation_requests_total = Counter("validator_requests_total", "Validaciones procesadas")
validation_ok_total = Counter("validator_ok_total", "Validaciones sin divergencia")
validation_divergence_total = Counter("validator_divergence_total", "Divergencias detectadas")
validator_heartbeat_total = Counter("validator_heartbeat_total", "Pong emitidos por validador")
retired_calculators_total = Counter(
    "validator_retired_calculators_total",
    "Calculadoras retiradas logicamente por divergencia",
    ["calculator"],
)
active_calculators_gauge = Gauge("validator_active_calculators", "Cantidad de calculadoras activas")

_publish_lock = threading.Lock()
_publish_channel = None
_retired_calculators = set()


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

    # DLQ central (la usamos también cuando hay caída de consumidor)
    channel.exchange_declare(exchange="payments.dlq", exchange_type="direct", durable=True)
    channel.queue_declare(queue="payments.dlq", durable=True)
    channel.queue_bind(exchange="payments.dlq", queue="payments.dlq", routing_key="payment.failed")

    # Cola de validación (si validador cae, TTL -> DLQ para backlog controlado)
    channel.queue_declare(
        queue="validator.requested",
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
        queue="validator.requested",
        routing_key="payment.requested",
    )

    channel.queue_declare(queue="validator.monitor", durable=True)
    channel.queue_bind(
        exchange="control.ping",
        queue="validator.monitor",
        routing_key="health.ping",
    )


def connect_publish_channel():
    global _publish_channel
    while _publish_channel is None:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            _publish_channel = channel
            app.logger.info("Canal RabbitMQ de publicacion listo en validador")
        except Exception as exc:
            app.logger.warning("Esperando RabbitMQ para publicar (validador): %s", exc)
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


def calculator_result(calc_name: str, amount: float) -> float:
    if calc_name == FAULTY_CALCULATOR:
        return round(amount + FAULTY_DELTA, 2)
    return round(amount, 2)


def execute_voting(amount: float):
    available = [c for c in ["calc_a", "calc_b", "calc_c"] if c not in _retired_calculators]
    if len(available) < 2:
        # Fallback de seguridad: si se retiraron 2 calculadoras, vuelve a habilitarlas.
        _retired_calculators.clear()
        available = ["calc_a", "calc_b", "calc_c"]

    results = {calc: calculator_result(calc, amount) for calc in available}
    grouped = {}
    for calc, value in results.items():
        grouped.setdefault(value, []).append(calc)

    majority_value = max(grouped, key=lambda value: len(grouped[value]))
    majority_group = grouped[majority_value]

    divergence = len(grouped) > 1
    retired_now = []
    if divergence:
        for value, calculators in grouped.items():
            if value != majority_value:
                for calc in calculators:
                    if calc not in _retired_calculators:
                        _retired_calculators.add(calc)
                        retired_calculators_total.labels(calculator=calc).inc()
                        retired_now.append(calc)

    active_calculators_gauge.set(len([c for c in ["calc_a", "calc_b", "calc_c"] if c not in _retired_calculators]))
    return {
        "results": results,
        "majorityValue": majority_value,
        "majorityGroup": majority_group,
        "divergence": divergence,
        "retiredNow": retired_now,
        "activeCalculators": [c for c in ["calc_a", "calc_b", "calc_c"] if c not in _retired_calculators],
    }


def on_validation_requested(ch, method, _properties, body):
    try:
        event = json.loads(body.decode("utf-8"))
        reservation_id = event.get("reservationId")
        original_amount = float(event.get("amount", 0.0))
        correlation_id = event.get("correlationId", reservation_id)
        validation_requests_total.inc()

        vote = execute_voting(original_amount)

        # 1) Evento que habilita el cobro real (pagos consume ESTE)
        publish(
            "booking.events",
            "payment.validated",
            {
                "eventType": "PaymentValidated",
                "reservationId": reservation_id,
                "correlationId": correlation_id,
                "amount": vote["majorityValue"],          # monto final por mayoría (2/3)
                "originalAmount": original_amount,
                "divergence": vote["divergence"],
                "retiredCalculators": vote["retiredNow"],
                "activeCalculators": vote["activeCalculators"],
                "timestamp": now_iso(),
            },
        )

        # 2) Telemetría/alertas: se mantiene el stream para la validación
        base_payload = {
            "reservationId": reservation_id,
            "correlationId": correlation_id,
            "amount": original_amount,
            "majorityValue": vote["majorityValue"],
            "activeCalculators": vote["activeCalculators"],
            "timestamp": now_iso(),
        }

        if vote["divergence"]:
            validation_divergence_total.inc()
            publish(
                "payments.events",
                "validation.divergence",
                {
                    "eventType": "ValidationDivergenceAlert",
                    "resultsByCalculator": vote["results"],
                    "retiredCalculators": vote["retiredNow"],
                    **base_payload,
                },
            )
        else:
            validation_ok_total.inc()
            publish(
                "payments.events",
                "validation.succeeded",
                {
                    "eventType": "ValidationSucceeded",
                    "resultsByCalculator": vote["results"],
                    **base_payload,
                },
            )
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
        validator_heartbeat_total.inc()
    finally:
        ch.basic_ack(delivery_tag=method.delivery_tag)


def consumer_worker():
    while True:
        try:
            connection = rabbit_connection()
            channel = connection.channel()
            setup_topology(channel)
            channel.basic_qos(prefetch_count=20)
            channel.basic_consume(queue="validator.requested", on_message_callback=on_validation_requested)
            channel.basic_consume(queue="validator.monitor", on_message_callback=on_health_ping)
            app.logger.info("Consumidor RabbitMQ de validador activo")
            channel.start_consuming()
        except Exception as exc:
            app.logger.warning("Consumidor validador reiniciando: %s", exc)
            time.sleep(2)


@app.get("/status")
def status():
    return jsonify(
        {
            "service": APP_NAME,
            "faultyCalculator": FAULTY_CALCULATOR,
            "retiredCalculators": sorted(list(_retired_calculators)),
            "activeCalculators": [c for c in ["calc_a", "calc_b", "calc_c"] if c not in _retired_calculators],
        }
    )


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def bootstrap():
    active_calculators_gauge.set(3)
    connect_publish_channel()
    thread = threading.Thread(target=consumer_worker, daemon=True)
    thread.start()


if __name__ == "__main__":
    bootstrap()
    app.run(host="0.0.0.0", port=PORT)
