# Evidencias recolectadas

## Resumen rapido

| Indicador | Valor |
|---|---:|
| k6 normal - requests | 901 |
| k6 normal - error rate | 0.00% |
| k6 normal - p95 | 146.87ms |
| k6 proveedor caido - requests | 931 |
| k6 proveedor caido - error rate | 0.00% |
| k6 proveedor caido - p95 | 37.33ms |
| MTTD monitor (s) | 14.16 |
| Cola payments.requested (messages) | 0 |
| Cola payments.dlq (messages) | 21 |
| Estado reserva normal | CONFIRMED |
| Estado reserva proveedor caido | PAYMENT_FAILED |
| Evento de validador capturado | ValidationDivergenceAlert |
| Divergencias detectadas (`validator_divergence_total`) | 1.0 |
| Retiro logico de `calc_c` | 1.0 |
| Calculadoras activas tras votacion | 2.0 |
| Calculadoras retiradas (`/status`) | calc_c |

## Archivos para adjuntar

- `evidencias/k6/k6-escenario-normal.txt`
- `evidencias/k6/k6-escenario-proveedor-caido.txt`
- `evidencias/monitor/mttd.txt`
- `evidencias/monitor/monitor-timeline-caida-pagos.csv`
- `evidencias/monitor/monitor-status-antes-caida.json`
- `evidencias/monitor/monitor-status-deteccion.json`
- `evidencias/monitor/monitor-status-recuperacion.json`
- `evidencias/rabbitmq/queue-payments-requested.json`
- `evidencias/rabbitmq/queue-payments-dlq.json`
- `evidencias/rabbitmq/queue-validation-audit.json`
- `evidencias/validador/validation-audit-messages.json`
- `evidencias/validador/validador-status.json`
- `evidencias/validador/validador-metrics.txt`
- `evidencias/prometheus/prometheus-targets.json`
- `evidencias/estados/reserva-creada-normal.json`
- `evidencias/estados/reserva-estado-normal.json`
- `evidencias/estados/reserva-creada-proveedor-caido.json`
- `evidencias/estados/reserva-estado-proveedor-caido.json`
- `evidencias/comandos/docker-ps.txt`
- `evidencias/logs/reservas.log`
- `evidencias/logs/pagos.log`
- `evidencias/logs/monitor.log`
- `evidencias/logs/validador.log`
- `evidencias/logs/rabbitmq.log`
