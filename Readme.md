# TravelHub – Experimento 1 (EDA + Disponibilidad)

Infraestructura local para validar disponibilidad en el flujo `crear reserva → solicitar pago` con desacoplamiento por eventos, monitoreo por heartbeat y votación 2/3.

- **Broker:** RabbitMQ
- **Persistencia:** PostgreSQL + Redis
- **Observabilidad:** Prometheus + Alertmanager + Grafana + Loki + Promtail + Exporters
- **Testing:** WireMock + Toxiproxy + k6

> **Credenciales (solo laboratorio):** Postgres `postgres/admin` · Redis `admin` · Grafana `admin/admin` · RabbitMQ `guest/guest`

---

## Requisitos

- Docker Desktop en ejecución (`docker version && docker compose version`)
- WSL2 activo con integración habilitada (Windows)

---

## Estructura esperada

```
.
├── databases/
│   ├── postgresql/docker-compose.yml
│   └── redis/docker-compose.yml
├── rabbit/docker-compose.yml
├── observability/
│   ├── docker-compose.observability.yml
│   ├── prometheus/{prometheus.yml, alert_rules.yml}
│   ├── alertmanager/alertmanager.yml
│   ├── grafana/provisioning/{datasources,dashboards}/
│   ├── loki/config.yml
│   └── promtail/config.yml
├── testing/
│   ├── docker-compose.testing.yml
│   └── wiremock/mappings/{pay-ok.json, pay-fail.json}
└── services/docker-compose.services.yml
```

---

## Red compartida 

```bash
docker network create santinet 2>/dev/null || true
```

> Si se cambia el nombre, actualizar todos los `docker-compose.yml` que referencian `santinet`.

---

## Quickstart 

```bash
docker network create santinet 2>/dev/null || true
docker compose -f databases/postgresql/docker-compose.yml up -d
docker compose -f databases/redis/docker-compose.yml up -d
docker compose -f rabbit/docker-compose.yml up -d
docker compose -f observability/docker-compose.observability.yml up -d
docker compose -f testing/docker-compose.testing.yml up -d
docker compose -f services/docker-compose.services.yml up -d --build
```

**Validación:** `http://localhost:9090/targets` · `http://localhost:3000` · `http://localhost:8083/status` · `http://localhost:8084/status`

---

## Despliegue paso a paso

### PostgreSQL
```bash
cd databases/postgresql && docker compose up -d
```
`localhost:5433` · DB `d2b` · `postgres/admin`

### Redis
```bash
cd databases/redis && docker compose up -d
```
`localhost:6380` · password `admin`

### RabbitMQ
```bash
cd rabbit && docker compose up -d
```
AMQP `localhost:5672` · UI `http://localhost:15672` · `guest/guest`

### Observabilidad
```bash
cd observability && docker compose -f docker-compose.observability.yml up -d
```
| Servicio | URL |
|---|---|
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Grafana | `http://localhost:3000` |
| Loki | `http://localhost:3100` |

### Testing
```bash
cd testing
docker compose -f docker-compose.testing.yml pull toxiproxy
docker compose -f docker-compose.testing.yml up -d
```
WireMock `http://localhost:8089` · Toxiproxy API `http://localhost:8474`  
> `k6` se ejecuta bajo demanda con `profiles: ["manual"]`.

### Slice de aplicación
```bash
cd services && docker compose -f docker-compose.services.yml up -d --build
```
Reservas `:8081` · Pagos `:8082` · Monitor `:8083` · Validador `:8084`

---

## Corrida del experimento

```bash
# 1) Escenario normal
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js

# 2) Falla de proveedor (reservas no debe bloquearse)
docker stop wiremock
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js
docker start wiremock

# 3) Detección por monitor (MTTD ≤ 20s)
docker stop pagos
curl http://localhost:8083/status
docker start pagos

# 4) Votación 2/3
curl -X POST http://localhost:8081/reservas -H "Content-Type: application/json" \
     -d '{"userId":"validator-test","amount":200.00}'
curl http://localhost:8084/status
```

**Resultados esperados:**
- `reservas` responde 202 durante falla del proveedor
- `monitor` detecta degradación de `pagos` en ≤ 20s
- `validador` detecta divergencia y retira lógicamente `calc_c` (mayoría 2/3)

---

## Archivos clave

| Archivo | Descripción |
|---|---|
| `prometheus/prometheus.yml` | `scrape_interval: 10s`, jobs para exporters y slice de servicios |
| `prometheus/alert_rules.yml` | `ExporterDown` (up==0 por 20s) · `RabbitDLQGrowing` |
| `wiremock/mappings/pay-ok.json` | `POST /pay` → 200 APPROVED |
| `wiremock/mappings/pay-fail.json` | `POST /pay-fail` → 500 ERROR |

---

## Operación

```bash
# Estado
docker ps && docker network inspect santinet | head

# Logs
docker logs -f <reservas|pagos|monitor|validador|prometheus|grafana|loki|wiremock|toxiproxy>

# Apagar todo
docker compose -f services/docker-compose.services.yml down
docker compose -f testing/docker-compose.testing.yml down
docker compose -f observability/docker-compose.observability.yml down
docker compose -f rabbit/docker-compose.yml down
docker compose -f databases/redis/docker-compose.yml down
docker compose -f databases/postgresql/docker-compose.yml down

# Borrar volúmenes (destructivo)
docker volume rm observability_prometheus_data observability_grafana_data \
  observability_alertmanager_data observability_loki_data redis_redis_data 2>/dev/null || true
```

---

## Troubleshooting

| Problema | Solución |
|---|---|
| `network santinet not found` | `docker network create santinet` |
| `port is already allocated` | Cambiar puerto host en `ports:` del compose afectado (ej. `5433→5434`) |

| Exporters DOWN en Prometheus | Verificar que todos estén en `santinet`; revisar hostnames (`postgres`, `redis-server`, `rabbitmq`) en `http://localhost:9090/targets` |
