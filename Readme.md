# TravelHub – Experimento 1 (EDA + Disponibilidad) – Infra local con Docker Compose

Este repositorio levanta en local la infraestructura del experimento:

- **Broker (Event Bus):** RabbitMQ
- **Persistencia:** PostgreSQL (reservas) + Redis (idempotencia/caché)
- **Observabilidad:** Prometheus + Alertmanager + Grafana + Exporters + Loki + Promtail
- **Testing (inyección de fallas y carga):** WireMock + Toxiproxy + k6

> ⚠️ **Credenciales por defecto (solo laboratorio):**
> - Postgres: `postgres/admin`
> - Redis: password `admin`
> - Grafana: `admin/admin`
> - RabbitMQ: `guest/guest`

---

## Contenido

- Requisitos
- Estructura esperada
- Red Docker compartida
- Quickstart (2-5 min)
- Despliegue paso a paso
- Corrida del experimento
- Operación y troubleshooting

---

## 1) Requisitos

### Windows (WSL2)
1. Docker Desktop instalado.
2. WSL2 activo + integración habilitada:
   - Docker Desktop → **Settings** → **Resources** → **WSL integration** → habilitar la distribución correspondiente.
3. En WSL:
```bash
docker version
docker compose version
````

### macOS

1. Docker Desktop instalado y corriendo.
2. Verifica:

```bash
docker version
docker compose version
```

---

## 2) Estructura de carpetas esperada

```
.
├── databases
│   ├── postgresql
│   │   ├── docker-compose.yml
│   │   └── postgres-scripts/                 # opcional init scripts
│   └── redis
│       └── docker-compose.yml
├── rabbit
│   └── docker-compose.yml
├── observability
│   ├── docker-compose.observability.yml
│   ├── alertmanager
│   │   └── alertmanager.yml
│   ├── prometheus
│   │   ├── prometheus.yml
│   │   └── alert_rules.yml
│   ├── grafana
│   │   └── provisioning
│   │       ├── datasources
│   │       │   └── datasources.yml
│   │       └── dashboards
│   │           └── dashboards.yml
│   ├── loki
│   │   └── config.yml
│   └── promtail
│       └── config.yml
└── testing
    ├── docker-compose.testing.yml
    └── wiremock
        └── mappings
            ├── pay-ok.json
            └── pay-fail.json
```

---

## 3) Red Docker compartida (OBLIGATORIA)

Todos los contenedores usan una red externa llamada **`santinet`**. Crear una sola vez:

```bash
docker network create santinet 2>/dev/null || true
docker network ls | grep santinet
```

> Si se cambia el nombre de la red, se deben actualizar los `docker-compose.yml` que apuntan a `santinet`.

---

## 3.1) Inicio rápido (2-5 min)

Para levantar el entorno de forma rápida sin recorrer cada subsección:

```bash
# desde la raíz del repo
docker network create santinet 2>/dev/null || true

docker compose -f databases/postgresql/docker-compose.yml up -d
docker compose -f databases/redis/docker-compose.yml up -d
docker compose -f rabbit/docker-compose.yml up -d
docker compose -f observability/docker-compose.observability.yml up -d
docker compose -f testing/docker-compose.testing.yml up -d
docker compose -f services/docker-compose.services.yml up -d --build

docker ps
```

Validación rápida:

- Prometheus targets: `http://localhost:9090/targets`
- Grafana: `http://localhost:3000`
- Monitor status: `http://localhost:8083/status`
- Validador status: `http://localhost:8084/status`

---

## 4) Despliegue paso a paso (orden recomendado)

### 4.1 PostgreSQL

```bash
cd databases/postgresql
docker compose up -d
docker ps | grep postgres
```

**Acceso local**

* Host: `localhost`
* Puerto: `5433`
* DB: `d2b`
* Usuario/Pass: `postgres/admin`

---

### 4.2 Redis

```bash
cd ../redis
docker compose up -d
docker ps | grep redis
```

**Acceso local**

* Host: `localhost`
* Puerto: `6380`
* Password: `admin`

> Nota: el password lo impone `--requirepass admin`.

---

### 4.3 RabbitMQ

```bash
cd ../../rabbit
docker compose up -d
docker ps | grep rabbitmq
```

**Acceso local**

* AMQP: `localhost:5672`
* UI: `http://localhost:15672`
* Usuario/Pass: `guest/guest`

---

### 4.4 Observabilidad (Prometheus + Grafana + Alertas + Logs)

```bash
cd ../observability
docker compose -f docker-compose.observability.yml up -d
docker ps | egrep "prometheus|grafana|alertmanager|loki|promtail|_exporter"
```

**URLs**

* Prometheus: `http://localhost:9090`
* Alertmanager: `http://localhost:9093`
* Grafana: `http://localhost:3000` (admin/admin)
* Loki: `http://localhost:3100`

**Validación rápida**

* Prometheus targets: `http://localhost:9090/targets` (debe mostrar exporters en estado **UP**)
* Grafana: ya provisionado con datasources Prometheus y Loki

---

### 4.5 Testing (WireMock + Toxiproxy + k6)

```bash
cd ../testing
docker compose -f docker-compose.testing.yml down
docker compose -f docker-compose.testing.yml pull toxiproxy
docker compose -f docker-compose.testing.yml up -d
docker ps | egrep "wiremock|toxiproxy|k6"
```

**Acceso local**

* WireMock: `http://localhost:8089`
* Toxiproxy API: `http://localhost:8474`

`k6` está definido con `profiles: ["manual"]`, por lo que su uso previsto es bajo demanda (ejecución manual), en lugar de ejecutarse de forma permanente.

---

### 4.6 Slice de aplicación (Reservas + Pagos + Monitor + Validador)

```bash
cd ../services
docker compose -f docker-compose.services.yml up -d --build
docker ps | egrep "reservas|pagos|monitor|validador"
```

**Acceso local**

* Reservas API: `http://localhost:8081`
* Pagos API: `http://localhost:8082`
* Monitor API: `http://localhost:8083`
* Validador API: `http://localhost:8084`

---

### 4.7 Corrida del experimento completo (incluye votación 2/3)

```bash
# 1) Escenario normal (proveedor OK)
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js

# 2) Escenario falla de proveedor (sin bloquear reservas)
docker stop wiremock
docker compose -f testing/docker-compose.testing.yml --profile manual run --rm k6 run /scripts/reservas-smoke.js
docker start wiremock

# 3) Escenario monitor (MTTD <= 20s)
docker stop pagos
curl http://localhost:8083/status
docker start pagos

# 4) Escenario votación 2/3 (validador)
curl -X POST http://localhost:8081/reservas -H "Content-Type: application/json" -d '{"userId":"validator-test","amount":200.00}'
curl http://localhost:8084/status
```

**Validaciones esperadas de la corrida**

* `reservas` mantiene disponibilidad (202) en falla de proveedor.
* `monitor` detecta `pagos` degradado dentro de la ventana de 20s.
* `validador` detecta divergencia y retira lógicamente `calc_c` (mayoría 2/3).

---

## 5) ¿Qué hace cada componente?

### Databases

* **PostgreSQL:** base de reservas (DB `d2b`).
* **Redis:** soporte de idempotencia/caché (password requerido).

### Broker

* **RabbitMQ:** event bus para desacoplar flujos (AMQP + consola de administración).

### Observabilidad

* **Prometheus:** recolecta métricas cada 10s (scrape) y evalúa reglas de alerta.
* **Alertmanager:** recibe alertas disparadas por Prometheus.
* **Grafana:** dashboards (datasources provisionados).
* **Exporters:** exponen métricas de Postgres/Redis/RabbitMQ sin instrumentar código.
* **Loki + Promtail:** centralización de logs (Promtail lee logs y los envía a Loki).

### Testing

* **WireMock:** stub del proveedor de pagos, responde con escenarios controlados:

  * `POST /pay` → **200 APPROVED**
  * `POST /pay-fail` → **500 ERROR**
* **Toxiproxy:** permite simular fallas de red (latencia, cortes, timeouts) entre servicios y el proveedor/mock.
* **k6:** generador de carga para probar throughput/latencia del flujo.

---

## 6) Explicación de archivos clave

### 6.1 `observability/prometheus/prometheus.yml`

* Define `scrape_interval: 10s` y `evaluation_interval: 10s`.
* Configura `alertmanagers` y los `scrape_configs` de exporters (postgres/redis/rabbit).
* Incluye el job `travelhub_services` para scrapear `/metrics` en `reservas`, `pagos`, `monitor` y `validador`.

### 6.2 `observability/prometheus/alert_rules.yml`

* `ExporterDown`: alerta si `up == 0` por `20s`.
* `RabbitDLQGrowing`: alerta si crece una cola con nombre que matchee `.*DLQ.*`.

### 6.3 `observability/docker-compose.observability.yml`

* Levanta Prometheus, Alertmanager, Grafana, exporters, Loki y Promtail.
* Todos conectados a la red externa `santinet` para resolverse por nombre.

### 6.4 `testing/wiremock/mappings/*.json`

* Define “mappings” de endpoints para simular proveedor de pagos:

  * `pay-ok.json` y `pay-fail.json`.

### 6.5 `testing/docker-compose.testing.yml`

* Levanta WireMock y Toxiproxy (y k6 bajo `profile: manual`).
* Red externa `santinet` para integrarse con el resto del stack.

---

## 7) Operación: comandos útiles

### Ver estado

```bash
docker ps
docker network inspect santinet | head
```

### Ver logs

```bash
docker logs -f postgres
docker logs -f redis-server
docker logs -f rabbitmq
docker logs -f reservas
docker logs -f pagos
docker logs -f monitor
docker logs -f validador
docker logs -f prometheus
docker logs -f grafana
docker logs -f loki
docker logs -f promtail
docker logs -f wiremock
docker logs -f toxiproxy
```

### Apagar stacks

```bash
# DBs
cd databases/postgresql && docker compose down
cd ../redis && docker compose down

# Rabbit
cd ../../rabbit && docker compose down

# Observabilidad
cd ../observability && docker compose -f docker-compose.observability.yml down

# Testing
cd ../testing && docker compose -f docker-compose.testing.yml down

# Slice app (reservas/pagos/monitor/validador)
cd ../services && docker compose -f docker-compose.services.yml down
```

### Borrar datos persistidos (¡destructivo!)

```bash
docker volume rm observability_prometheus_data observability_grafana_data observability_alertmanager_data observability_loki_data 2>/dev/null || true
docker volume rm redis_redis_data 2>/dev/null || true
```

---

## 8) Troubleshooting

### “network santinet not found” / “invalid compose project”

```bash
docker network create santinet
```

### “port is already allocated”

Cambiar el puerto host (lado izquierdo) en `ports:`:

* Postgres: `"5433:5432"` → `"5434:5432"`
* Redis: `"6380:6379"` → `"6381:6379"`
* Grafana: `"3000:3000"` → `"3001:3000"`
* WireMock: `"8089:8080"` → `"8090:8080"`

### Exporters DOWN en Prometheus

1. Confirma que todos estén en la misma red `santinet`.
2. Verifica los hostnames usados en exporters:

   * Postgres: `postgres`
   * Redis: `redis-server`
   * Rabbit: `rabbitmq`
3. Revisa `http://localhost:9090/targets` para ver el error exacto.

---

## 9) Nota sobre configuración

Las configuraciones completas ya están versionadas en el repositorio.  
Para referencia, revisa directamente:

- `databases/**/docker-compose.yml`
- `rabbit/docker-compose.yml`
- `observability/**`
- `testing/**`
- `services/docker-compose.services.yml`

