# TravelHub – Experimento 1 (EDA + Disponibilidad)

Infraestructura local para validar disponibilidad en el flujo `crear reserva → solicitar pago` con:
- **Desacoplamiento por eventos (RabbitMQ)**
- **Monitor por heartbeat (Ping/Pong cada 10s, detección ≤ 20s)**
- **Votación 2/3 (retiro lógico de calculadora divergente)**
- **Backlog controlado con TTL + DLQ**
- **Observabilidad con Prometheus + Grafana + Alertmanager + Loki/Promtail + Exporters**
- **Testing con WireMock + Toxiproxy + k6**

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
│   ├── k6/reservas-smoke.js
│   └── wiremock/mappings/{pay-ok.json, pay-fail.json}
└── services/
    ├── docker-compose.services.yml
    ├── reservas/
    ├── pagos/
    ├── monitor/
    └── validador/

````

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

**Validación rápida**

* Prometheus targets: `http://localhost:9090/targets`
* Grafana: `http://localhost:3000`
* Monitor status: `http://localhost:8083/status`
* Validador status: `http://localhost:8084/status`

---

## Flujo del experimento (slice)

1. `reservas` crea reserva y publica `payment.requested` (async).
2. `validador` consume `payment.requested`, ejecuta votación 2/3 y:

   * publica `payment.validated` con el **monto por mayoría**
   * si hay divergencia, “retira” la calculadora divergente
3. `pagos` consume `payment.validated` y procesa el cobro contra el proveedor (WireMock).
4. `reservas` consume `payment.succeeded` / `payment.failed` y actualiza el estado de la reserva.

**Backlog controlado:** las colas críticas tienen **TTL (30s) + DLQ**, evitando crecimiento ilimitado cuando cae un consumidor.

---

## Despliegue paso a paso

### PostgreSQL
```bash
docker compose -f databases/postgresql/docker-compose.yml up -d
```
`localhost:5433` · DB `d2b` · `postgres/admin`

### Redis
```bash
docker compose -f databases/redis/docker-compose.yml up -d
```
`localhost:6380` · password `admin`

### RabbitMQ
```bash
docker compose -f rabbit/docker-compose.yml up -d
```
AMQP `localhost:5672` · UI `http://localhost:15672` · `guest/guest`

### Observabilidad
```bash
docker compose -f observability/docker-compose.observability.yml up -d
```

| Servicio | URL |
|---|---|
| Prometheus   | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Grafana      | `http://localhost:3000` |
| Loki         | `http://localhost:3100` |

### Testing

```bash
docker compose -f testing/docker-compose.testing.yml up -d
```

WireMock `http://localhost:8089` · Toxiproxy API `http://localhost:8474`

> `k6` se ejecuta bajo demanda con `profiles: ["manual"]`.

### Slice de aplicación
```bash
docker compose -f services/docker-compose.services.yml up -d --build
```
Reservas `:8081` · Pagos `:8082` · Monitor `:8083` · Validador `:8084`

---

## Corrida del experimento

### 1) Escenario normal

```bash
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js
```

### 2) Falla del proveedor (reservas no debe bloquearse)

```bash
docker stop wiremock
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js
docker start wiremock
```

**Esperado**

* `reservas` responde 202 durante la falla
* `pagos` publica `payment.failed` y manda a DLQ
* DLQ crece (validable por UI de RabbitMQ y métricas)

### 3) Detección por monitor (MTTD ≤ 20s) + backlog controlado

```bash
docker stop pagos
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js
curl http://localhost:8083/status
docker start pagos
```

**Esperado**

* `monitor` marca `pagos` unhealthy en ≤ 20s
* La cola `payments.validated` no crece infinito: TTL → DLQ

### 4) Votación 2/3 y retiro lógico

```bash
curl -X POST http://localhost:8081/reservas -H "Content-Type: application/json" \
     -d '{"userId":"validator-test","amount":200.00}'
curl http://localhost:8084/status
```

**Esperado**

* `validador` detecta divergencia y retira `calc_c`
* `pagos` procesa el cobro usando el monto por mayoría (evento `payment.validated`)

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

## [Video de corrida](https://vimeo.com/1166528587?share=copy&fl=sv&fe=ci)

