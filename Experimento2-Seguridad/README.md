# TravelHub – Experimento 2 (Seguridad)

Infraestructura local para validar integridad en tránsito en el flujo `cliente -> gateway -> verifier -> reservations`, con:
- **Validación de integridad** por `quoteHash` y `sig`
- **Detección de replay** por `nonce + timestamp + TTL`
- **Bloqueo temprano** antes de llegar a `reservations`
- **Audit Log** persistido en MongoDB
- **Observabilidad** con Prometheus + Grafana + Alertmanager + Loki/Promtail

> **Credenciales (solo laboratorio):** MongoDB `admin/admin` · Grafana `admin/admin` · HMAC `super-secret-exp2-key`

---

## Requisitos

- Docker Desktop en ejecución (`docker version && docker compose version`)
- WSL2 activo con integración habilitada (Windows)

---

## Estructura esperada

```text
.
├── databases/
│   └── mongodb/docker-compose.yml
├── observability/
│   ├── docker-compose.observability.yml
│   ├── prometheus/{prometheus.yml, alert_rules.yml}
│   ├── alertmanager/alertmanager.yml
│   ├── grafana/provisioning/{datasources,dashboards}/
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
├── docs/reservation-contract.md
├── evidencias/
├── Informe-Resultados-Experimento2.md
└── README.md
```

---

## Red compartida

```bash
docker network create travelhubsecnet 2>/dev/null || true
```

---

## Quickstart

```bash
docker network create travelhubsecnet 2>/dev/null || true
docker compose -f databases/mongodb/docker-compose.yml up -d
docker compose -f observability/docker-compose.observability.yml up -d
docker compose -f services/docker-compose.services.yml up -d --build
```

**Validación rápida**

- Gateway: `http://localhost:8080/health`
- Audit Log: `http://localhost:8084/health`
- Prometheus targets: `http://localhost:9090/targets`
- Grafana: `http://localhost:3000`

---

## Flujo del experimento

1. El cliente construye la solicitud con `requestId`, `nonce`, `timestamp`, `quoteHash` y `sig`.
2. `gateway` recibe la petición y propaga `requestId`.
3. `verifier` valida:
   - integridad del payload (`quoteHash`)
   - autenticidad del mensaje (`sig`)
   - unicidad del `nonce`
   - vigencia temporal (`timestamp`)
4. Solo si la solicitud es válida, `verifier` la reenvía a `reservations`.
5. `reservations` persiste la reserva en SQLite.
6. `verifier` registra la evidencia en `auditlog`.
7. `auditlog` persiste el evento en MongoDB.

**Regla de seguridad principal:** si falla cualquier validación, la solicitud se rechaza y no se procesa en `reservations`.

---

## Despliegue paso a paso

### MongoDB

```bash
docker compose -f databases/mongodb/docker-compose.yml up -d
```

`localhost:27017` · `admin/admin`

### Observabilidad

```bash
docker compose -f observability/docker-compose.observability.yml up -d
```

| Servicio | URL |
|---|---|
| Prometheus | `http://localhost:9090` |
| Alertmanager | `http://localhost:9093` |
| Grafana | `http://localhost:3000` |
| Loki | `http://localhost:3100` |

### Slice de aplicación

```bash
docker compose -f services/docker-compose.services.yml up -d --build
```

Gateway `:8080` · Audit Log `:8084`

---

## Corrida del experimento

Ejecuta los scripts desde la raíz del proyecto.

### 1) Solicitud válida

```bash
python3 testing/send_valid_request.py
```

**Esperado**

- respuesta `201`
- la reserva queda registrada
- existe evidencia `REQUEST_ACCEPTED`

### 2) Tampering

```bash
python3 testing/send_tampered_request.py
```

**Esperado**

- respuesta `422`
- la reserva no se crea
- aparece `INTEGRITY_VIOLATION` en `Audit Log`

### 3) Replay

```bash
python3 testing/send_replay_request.py
```

**Esperado**

- primer envío `201`
- segundo envío `409`
- el segundo intento queda como `REPLAY_DETECTED` en `Audit Log`

---

## Verificación

### Consulta por `requestId`

```bash
curl http://localhost:8080/api/v1/reservations/by-request/<REQUEST_ID>
curl "http://localhost:8084/logs?requestId=<REQUEST_ID>"
```

### Qué debe verse

- caso válido: reserva persistida + evento `REQUEST_ACCEPTED`
- tampering: consulta `404` + evento `INTEGRITY_VIOLATION`
- replay: una sola reserva efectiva + eventos `REQUEST_ACCEPTED` y `REPLAY_DETECTED`

---

## Métricas útiles

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

## Evidencias y reporte

- Índice de evidencias: `evidencias/EVIDENCIAS-INDEX.md`
- Resumen cuantitativo: `evidencias/escenarios/corrida-seguridad-resumen.json`
- Informe académico: `Informe-Resultados-Experimento2.md`

---

## Operación

```bash
# Estado
docker ps && docker network inspect travelhubsecnet | head

# Logs
docker logs -f exp2-gateway
docker logs -f exp2-verifier
docker logs -f exp2-reservations
docker logs -f exp2-auditlog
docker logs -f exp2-prometheus

# Apagar todo
docker compose -f services/docker-compose.services.yml down
docker compose -f observability/docker-compose.observability.yml down
docker compose -f databases/mongodb/docker-compose.yml down

# Borrar volúmenes (destructivo)
docker volume rm mongodb_mongodb_data 2>/dev/null || true
docker volume rm services_reservations_data 2>/dev/null || true
docker volume rm observability_prometheus_data observability_grafana_data \
  observability_alertmanager_data observability_loki_data 2>/dev/null || true
```

---

## Troubleshooting

| Problema | Solución |
|---|---|
| `network travelhubsecnet not found` | `docker network create travelhubsecnet` |
| `port is already allocated` | Cambiar el puerto host en el `ports:` del compose afectado |
| Targets DOWN en Prometheus | Verificar hostnames `gateway`, `verifier`, `reservations`, `auditlog`, `mongodb` en `http://localhost:9090/targets` |
| WSL no detecta Docker | Encender Docker Desktop y habilitar integración WSL |

---

## Resultado esperado final

Con esta infraestructura se demuestra que:

- una solicitud válida llega a `reservations`
- un payload adulterado se detecta antes de procesarse
- un replay se detecta antes de procesarse
- el `requestId` se propaga de extremo a extremo
- la evidencia principal queda registrada en `Audit Log`
