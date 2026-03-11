import os
import time
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest
from pymongo import ASCENDING, MongoClient

APP_NAME = "auditlog"
PORT = int(os.getenv("PORT", "8080"))
MONGO_URI = os.getenv("MONGO_URI", "mongodb://admin:admin@mongodb:27017/experiment2?authSource=admin")
DB_NAME = os.getenv("DB_NAME", "experiment2")
COLLECTION_NAME = os.getenv("COLLECTION_NAME", "audit_events")

app = Flask(__name__)

audit_events_received_total = Counter(
    "audit_events_received_total",
    "Eventos recibidos por el audit log",
    ["event_type"],
)
audit_events_persisted_total = Counter(
    "audit_events_persisted_total",
    "Eventos persistidos en MongoDB",
    ["event_type"],
)
audit_log_queries_total = Counter("audit_log_queries_total", "Consultas ejecutadas sobre el audit log")

_mongo_client = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mongo_client():
    global _mongo_client
    while _mongo_client is None:
        try:
            client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
            client.admin.command("ping")
            _mongo_client = client
        except Exception as exc:
            app.logger.warning("Esperando MongoDB para audit log: %s", exc)
            _mongo_client = None
            time.sleep(2)
    return _mongo_client


def collection():
    db = mongo_client()[DB_NAME]
    coll = db[COLLECTION_NAME]
    coll.create_index([("requestId", ASCENDING)])
    coll.create_index([("eventType", ASCENDING)])
    coll.create_index([("quoteId", ASCENDING)])
    coll.create_index([("timestamp", ASCENDING)])
    return coll


def normalize_event(event: dict) -> dict:
    actor = event.get("actor") or {}
    return {
        "eventType": event.get("eventType", "UNKNOWN"),
        "requestId": event.get("requestId"),
        "actor": {
            "userId": actor.get("userId"),
            "role": actor.get("role"),
        },
        "ip": event.get("ip"),
        "timestamp": event.get("timestamp") or now_iso(),
        "requestTimestamp": event.get("requestTimestamp"),
        "nonce": event.get("nonce"),
        "reason": event.get("reason"),
        "quoteId": event.get("quoteId"),
        "payloadHash": event.get("payloadHash"),
    }


@app.post("/events")
def create_event():
    payload = request.get_json(silent=True) or {}
    event = normalize_event(payload)
    event_type = event["eventType"]
    if not event["requestId"]:
        return jsonify({"error": "missing_request_id"}), 400

    audit_events_received_total.labels(event_type=event_type).inc()
    collection().insert_one(event)
    audit_events_persisted_total.labels(event_type=event_type).inc()
    return jsonify({"status": "stored", "eventType": event_type, "requestId": event["requestId"]}), 201


@app.get("/logs")
def get_logs():
    audit_log_queries_total.inc()
    filters = {}
    request_id = request.args.get("requestId")
    event_type = request.args.get("eventType")
    quote_id = request.args.get("quoteId")
    limit = min(int(request.args.get("limit", "100")), 500)

    if request_id:
        filters["requestId"] = request_id
    if event_type:
        filters["eventType"] = event_type
    if quote_id:
        filters["quoteId"] = quote_id

    events = list(collection().find(filters, {"_id": 0}).sort("timestamp", ASCENDING).limit(limit))
    return jsonify({"count": len(events), "events": events})


@app.get("/health")
def health():
    mongo_client().admin.command("ping")
    return jsonify({"status": "ok", "service": APP_NAME})


@app.get("/metrics")
def metrics():
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


if __name__ == "__main__":
    collection()
    app.run(host="0.0.0.0", port=PORT)
