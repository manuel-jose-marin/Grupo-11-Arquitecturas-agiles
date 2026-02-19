# Evidencias recolectadas

**Última corrida:** 2026-02-19

## Resumen rapido

| Indicador | Corrida 1 (2026-02-17) | Corrida 2 (2026-02-19) |
|---|---:|---:|
| k6 normal - requests | 901 | 761 |
| k6 normal - error rate | 0.00% | 0.00% |
| k6 normal - p95 | 146.87ms | 398.87ms (cold start) |
| k6 proveedor caido - requests | 931 | 877 |
| k6 proveedor caido - error rate | 0.00% | 0.00% |
| k6 proveedor caido - p95 | 37.33ms | 116.82ms |
| k6 proveedor caido - max | 498.4ms | 599.54ms |
| MTTD monitor (s) | 14.16 | 1.00 |
| Cola `payments.requested` (messages) | 0 | — (cola eliminada del flujo) |
| Cola `payments.validated` (messages al cierre) | — | 0 |
| Cola `payments.validated` (TTL argumentos) | — | x-message-ttl=30000, x-dead-letter-exchange=payments.dlq |
| Cola `validator.requested` (messages al cierre) | — | 0 |
| Cola `payments.dlq` (messages) | 21 | 1004 |
| Estado reserva normal | CONFIRMED | CONFIRMED |
| Estado reserva proveedor caido | PAYMENT_FAILED | PENDING_PAYMENT |
| Evento de validador capturado | ValidationDivergenceAlert | ValidationDivergenceAlert |
| `validator_requests_total` | 1.0 | 1642.0 |
| `validator_ok_total` | — | 1641.0 |
| Divergencias detectadas (`validator_divergence_total`) | 1.0 | 1.0 |
| Retiro logico de `calc_c` | 1.0 | 1.0 |
| Calculadoras activas tras votacion | 2.0 | 2.0 |
| Calculadoras retiradas (`/status`) | calc_c | calc_c |

## Archivos — corrida 2 (2026-02-19)

- `evidencias/k6/k6-escenario-normal.txt`
- `evidencias/k6/k6-escenario-proveedor-caido.txt`
- `evidencias/monitor/mttd.txt`
- `evidencias/monitor/monitor-timeline-caida-pagos.csv`
- `evidencias/monitor/monitor-status-antes-caida.json`
- `evidencias/monitor/monitor-status-deteccion.json`
- `evidencias/monitor/monitor-status-recuperacion.json`
- `evidencias/rabbitmq/queue-payments-validated.json` ← nuevo (reemplaza payments.requested)
- `evidencias/rabbitmq/queue-validator-requested.json` ← nuevo
- `evidencias/rabbitmq/queue-payments-dlq.json`
- `evidencias/validador/reserva-creada-validator-test.json`
- `evidencias/validador/validador-status.json`
- `evidencias/validador/validador-metrics.txt`
- `evidencias/prometheus/prometheus-targets.json`
- `evidencias/estados/reserva-estado-normal.json`
- `evidencias/estados/reserva-estado-proveedor-caido.json`
- `evidencias/estados/reserva-creada-proveedor-caido.json`
- `evidencias/comandos/docker-ps.txt`
- `evidencias/comandos/health-reservas.json`
- `evidencias/comandos/health-pagos.json`
- `evidencias/comandos/health-monitor.json`
- `evidencias/comandos/health-validador.json`
- `evidencias/logs/reservas.log`
- `evidencias/logs/pagos.log`
- `evidencias/logs/monitor.log`
- `evidencias/logs/validador.log`
- `evidencias/logs/rabbitmq.log`

## Notas sobre cambios entre corridas

- `payments.requested` fue reemplazada por `payments.validated` como cola que consume `pagos` (ajuste de flujo del commit de Santiago).
- `validator.requested` y `payments.validated` ahora tienen TTL de 30s y dead-letter hacia `payments.dlq`.
- El volumen de `validator_requests_total` refleja que **todas** las reservas pasan por votación en la corrida 2.
- La DLQ acumula mensajes de pagos fallidos (falla de proveedor) + mensajes expirados por TTL durante caída de `pagos`.
