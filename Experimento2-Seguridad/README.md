# TravelHub – Experimento 2 (Seguridad: tampering + replay)

Infraestructura local completa para validar el flujo:

`Cliente -> Gateway/BFF -> Verifier -> Reservations`

con registro de evidencia en `Audit Log`, detección de alteración de parámetros críticos, detección de replay y observabilidad con Prometheus + Grafana.

## Objetivo del experimento

Demostrar que una solicitud de reserva:

- **válida** llega a `Reservations`
- **adulterada** es detectada antes de procesarse
- **repetida (replay)** es detectada antes de procesarse
- deja evidencia trazable en `Audit Log` con `requestId`, `quoteId`, `payloadHash`, `timestamp`, `actor`, `rol`, `IP` y motivo

---

## Arquitectura implementada

### Flujo principal

1. El cliente construye la solicitud con `requestId`, `nonce`, `timestamp`, `quoteHash` y `sig`
2. `Gateway/BFF` recibe la petición y propaga el `requestId`
3. `Verifier` valida:
   - integridad del payload (`quoteHash`)
   - sello criptográfico (`sig`)
   - unicidad del `nonce`
   - ventana temporal (`timestamp` + TTL)
4. Solo si todo es correcto, `Verifier` reenvía a `Reservations`
5. `Reservations` persiste la reserva en SQLite
6. `Verifier` registra la evidencia de seguridad en `Audit Log`
7. `Audit Log` persiste los eventos en MongoDB

### Componentes desplegados

- **Gateway/BFF**: Flask
- **Verifier de Integridad y Anti-Replay**: Flask + validación HMAC + nonces en memoria
- **Reservations**: Flask + SQLite
- **Audit Log**: Flask + MongoDB
- **MongoDB**: almacenamiento flexible para evidencia de auditoría
- **Prometheus + Grafana + Alertmanager + Loki + Promtail**: métricas, alertas y logs

---

## Credenciales de laboratorio

- **MongoDB**: `admin/admin`
- **Grafana**: `admin/admin`
- **Llave HMAC del experimento**: `super-secret-exp2-key`

> Estas credenciales son solo para laboratorio local.

---

## Requisitos

### WSL2 (Windows)

1. Docker Desktop instalado y en ejecución
2. Integración WSL habilitada
3. Verifica:

```bash
docker version
docker compose version
```

### macOS

1. Docker Desktop instalado y en ejecución
2. Verifica:

```bash
docker version
docker compose version
```

---

## Estructura esperada

```text
.
├── databases/
│   └── mongodb/
│       └── docker-compose.yml
├── observability/
│   ├── docker-compose.observability.yml
│   ├── alertmanager/alertmanager.yml
│   ├── prometheus/
│   │   ├── prometheus.yml
│   │   └── alert_rules.yml
│   ├── grafana/provisioning/
│   │   ├── datasources/datasources.yml
│   │   └── dashboards/dashboards.yml
│   ├── loki/config.yml
│   └── promtail/config.yml
├── services/
│   ├── docker-compose.services.yml
│   ├── gateway/
│   ├── verifier/
│   ├── reservations/
│   └── audit-log/
├── testing/
│   ├── common.py
│   ├── send_valid_request.py
│   ├── send_tampered_request.py
│   └── send_replay_request.py
├── docs/
│   └── reservation-contract.md
└── README.md
```

---

## Red Docker compartida

Todos los stacks usan una red externa llamada `travelhubsecnet`.

Créala una sola vez:

```bash
docker network create travelhubsecnet 2>/dev/null || true
docker network ls | grep travelhubsecnet
```

---

## Contrato de la solicitud

El contrato completo está en `docs/reservation-contract.md`.

Resumen de campos relevantes:

- `actor.userId`
- `actor.role`
- `reservation.hotelId`
- `reservation.roomType`
- `reservation.checkIn`
- `reservation.checkOut`
- `reservation.guests`
- `reservation.currency`
- `reservation.totalAmount`
- `reservation.cancellationPolicyId`
- `reservation.quoteId`
- `reservation.quoteHash`
- `meta.requestId`
- `meta.nonce`
- `meta.timestamp`
- `meta.sig`

### Qué protege cada campo

- `quoteHash`: detecta alteración sobre parámetros críticos de negocio
- `sig`: protege el mensaje completo canónico con HMAC-SHA256
- `nonce + timestamp`: permite detectar replay
- `requestId`: permite trazabilidad extremo a extremo

---

## Despliegue rápido

Ejecuta desde la raíz del proyecto:

```bash
docker network create travelhubsecnet 2>/dev/null || true

docker compose -f databases/mongodb/docker-compose.yml up -d
docker compose -f observability/docker-compose.observability.yml up -d
docker compose -f services/docker-compose.services.yml up -d --build
```

---

## Despliegue paso a paso

### 1. MongoDB

```bash
cd databases/mongodb
docker compose up -d
```

Acceso local:

- Host: `localhost`
- Puerto: `27017`
- Usuario: `admin`
- Password: `admin`

### 2. Observabilidad

```bash
cd ../observability
docker compose -f docker-compose.observability.yml up -d
```

Servicios:

- Prometheus: `http://localhost:9090`
- Alertmanager: `http://localhost:9093`
- Grafana: `http://localhost:3000`
- Loki: `http://localhost:3100`

### 3. Microservicios del experimento

```bash
cd ../services
docker compose -f docker-compose.services.yml up -d --build
```

Servicios expuestos al host:

- Gateway: `http://localhost:8080`
- Audit Log API: `http://localhost:8084`

Validación rápida:

```bash
curl http://localhost:8080/health
curl http://localhost:8084/health
curl http://localhost:9090/targets
```

---

## Qué hace cada servicio

### Gateway/BFF

- recibe la solicitud del cliente
- no recalcula firma ni hash
- propaga `requestId` extremo a extremo
- reenvía al `Verifier`

### Verifier

- valida `quoteHash`
- valida `sig`
- valida `nonce`
- valida `timestamp`
- corta el flujo si detecta tampering o replay
- registra evidencia en `Audit Log`

### Reservations

- solo acepta solicitudes con cabecera `X-Integrity-Verified=true`
- persiste reservas válidas en SQLite
- rechaza solicitudes directas no verificadas

### Audit Log

- recibe eventos desde `Verifier`
- guarda evidencia en MongoDB
- expone consulta REST de logs

---

## Ejecución del experimento

Ejecuta los scripts desde la raíz del proyecto.

### Escenario 1: solicitud válida

```bash
python3 testing/send_valid_request.py
```

Esperado:

- respuesta `201`
- la reserva queda registrada
- existe evidencia `REQUEST_ACCEPTED`

Consulta por `requestId`:

```bash
curl http://localhost:8080/api/v1/reservations/by-request/<REQUEST_ID>
curl "http://localhost:8084/logs?requestId=<REQUEST_ID>"
```

### Escenario 2: tampering ---- Validar

```bash
python3 testing/send_tampered_request.py
```

Esperado:

- respuesta `422`
- no se crea reserva
- aparece `INTEGRITY_VIOLATION` en `Audit Log`

### Escenario 3: replay ---- Validar

```bash
python3 testing/send_replay_request.py
```

Esperado:

- primer envío: `201`
- segundo envío: `409`
- el segundo intento queda como `REPLAY_DETECTED` en `Audit Log`

---

## Verificación de trazabilidad

El `requestId` debe poder seguirse en:

1. respuesta del cliente
2. Gateway
3. Verifier
4. Reservations
5. Audit Log

Consulta de ejemplo:

```bash
curl http://localhost:8080/api/v1/reservations/by-request/<REQUEST_ID>
curl "http://localhost:8084/logs?requestId=<REQUEST_ID>"
```

---

## Métricas útiles

En Prometheus/Grafana podrás revisar, entre otras:

- `gateway_requests_total`
- `verifier_requests_total`
- `verifier_integrity_violation_total`
- `verifier_replay_detected_total`
- `verifier_forwarded_total`
- `reservations_created_total`
- `reservations_rejected_total`
- `audit_events_received_total`
- `audit_events_persisted_total`

---

## Comandos útiles de operación

### Ver estado

```bash
docker ps
docker network inspect travelhubsecnet | head
```

### Ver logs

```bash
docker logs -f exp2-gateway
docker logs -f exp2-verifier
docker logs -f exp2-reservations
docker logs -f exp2-auditlog
docker logs -f exp2-prometheus
```

### Apagar todo

```bash
docker compose -f services/docker-compose.services.yml down
docker compose -f observability/docker-compose.observability.yml down
docker compose -f databases/mongodb/docker-compose.yml down
```

### Borrar datos persistidos (destructivo)

```bash
docker volume rm mongodb_mongodb_data 2>/dev/null || true
docker volume rm services_reservations_data 2>/dev/null || true
docker volume rm observability_prometheus_data observability_grafana_data observability_alertmanager_data observability_loki_data 2>/dev/null || true
```

---

## Troubleshooting

### Error de red

```bash
docker network create travelhubsecnet
```

### Puerto ocupado

Cambia el puerto del host en el `ports:` correspondiente.

Ejemplo:

- Gateway: `8080:8080` → `8081:8080`
- Grafana: `3000:3000` → `3001:3000`
- MongoDB: `27017:27017` → `27018:27017`

### Targets DOWN en Prometheus

Revisa:

```bash
curl http://localhost:9090/targets
```

Y verifica que los nombres DNS internos sean correctos:

- `gateway`
- `verifier`
- `reservations`
- `auditlog`
- `mongodb`

### WSL no detecta Docker

Asegura Docker Desktop encendido y con integración WSL habilitada.

---

## Resultado esperado final

Con esta infraestructura podrás demostrar que:

- una solicitud válida llega a `Reservations`
- un payload adulterado se detecta antes de procesarse
- un replay se detecta antes de procesarse
- el `requestId` se propaga de extremo a extremo
- la evidencia principal queda trazada en `Audit Log`
