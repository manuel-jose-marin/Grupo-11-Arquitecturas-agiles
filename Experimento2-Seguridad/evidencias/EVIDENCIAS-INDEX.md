# Evidencias recolectadas

**Última corrida:** 2026-03-10

## Resumen rapido

| Indicador | Corrida de evidencia (2026-03-10) |
|---|---:|
| Solicitudes válidas ejecutadas | 20 |
| Solicitudes válidas aceptadas (`201`) | 20 |
| Falsos positivos sobre válidas | 0 |
| Solicitudes adulteradas ejecutadas | 20 |
| Tampering detectado (`422`) | 20 |
| Solicitudes adulteradas no procesadas (`404` en consulta) | 20 |
| Escenarios replay ejecutados | 20 |
| Primer envío aceptado (`201`) | 20 |
| Segundo envío rechazado (`409`) | 20 |
| Evidencia de auditoría para válidas | 20 |
| Evidencia de auditoría para tampering | 20 |
| Evidencia de auditoría para replay | 20 |
| Targets de Prometheus relevantes en `up` | 4/4 |

## Archivos — corrida 2026-03-10

- `evidencias/comandos/fecha-ejecucion.txt`
- `evidencias/comandos/docker-ps.txt`
- `evidencias/comandos/health-gateway.json`
- `evidencias/comandos/health-verifier.json`
- `evidencias/comandos/health-reservations.json`
- `evidencias/comandos/health-auditlog.json`
- `evidencias/prometheus/prometheus-targets.json`
- `evidencias/metrics/gateway-metrics.txt`
- `evidencias/metrics/verifier-metrics.txt`
- `evidencias/metrics/reservations-metrics.txt`
- `evidencias/metrics/auditlog-metrics.txt`
- `evidencias/escenarios/corrida-seguridad-resumen.json`
- `evidencias/estados/reserva-valida.json`
- `evidencias/estados/reserva-tampering-no-procesada.json`
- `evidencias/estados/reserva-replay-estado-final.json`
- `evidencias/auditlog/valid-request-logs.json`
- `evidencias/auditlog/tampering-logs.json`
- `evidencias/auditlog/replay-logs.json`
- `evidencias/logs/gateway.log`
- `evidencias/logs/verifier.log`
- `evidencias/logs/reservations.log`
- `evidencias/logs/auditlog.log`

## Notas sobre interpretación

- La corrida cuantitativa del experimento usó `20` repeticiones por escenario: control, tampering y replay.
- Las tasas de detección y falsos positivos del informe se calculan con base en `evidencias/escenarios/corrida-seguridad-resumen.json`.
- Las métricas Prometheus son acumulativas del proceso; por eso muestran también la validación previa de humo ejecutada antes de la corrida cuantitativa.
- La evidencia principal del experimento permanece en el `Audit Log`, donde cada solicitud queda asociada a `requestId`, `quoteId`, `payloadHash`, actor, IP, `timestamp` y `reason`.
