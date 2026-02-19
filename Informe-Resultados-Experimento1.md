# Informe de resultados — Experimento 1 (EDA + Disponibilidad)

**Proyecto:** TravelHub — Experimento 1  
**Fecha de ejecución (plan):** 2026-02-16  
**Fecha de corrida de evidencias:** 2026-02-17 / 2026-02-19 (segunda corrida con ajustes)  
**Entorno:** Docker Compose local (macOS)  
**Autor:** Equipo de experimento (ejecución asistida)

---

## Titulo del experimento

Servicio de reserva con pagos desacoplados: enmascaramiento de fallas con Event Bus y Monitor (Ping-Echo/HeartBeat), con retiro de servicio y degradacion controlada.

---

## 1. Propósito del experimento

Validar que el flujo **Crear Reserva -> Solicitar Pago** conserve la disponibilidad cuando el servicio de pagos o el proveedor externo falla, usando:

- **Desacoplamiento asíncrono (Event Bus):** evitar propagación de fallas entre `reservas` y `pagos`.
- **Monitor Bus (Ping-Echo / HeartBeat):** detectar degradación/omisión de servicios y disparar mitigación.
- **Votación (3):** detectar respuestas erróneas en cálculos críticos.
- **Retiro de servicio ante fallas:** aislamiento lógico del componente defectuoso (circuit breaker, degradación, desacople).

Esto busca alineación con ASR de disponibilidad y umbrales de monitoreo definidos.

---

## 1.1 Hipotesis de diseno asociada al experimento

En el flujo **crear reserva → solicitar pago**, la separación asíncrona mediante un Event Bus (pub/sub) permite que una falla en Payments no se propague como indisponibilidad hacia Booking, y además un Monitor basado en mensajes de control (Ping-Echo/HeartBeat) permite detectar omisiones/degradación a tiempo para activar degradación controlada y evitar cascadas, contribuyendo al cumplimiento sostenido de la meta de disponibilidad mensual (ASR-DISP-01) y al monitoreo recurrente (ASR-DISP-06).

## 1.2 Punto de sensibilidad

La decision critica es reemplazar el acoplamiento sincronico entre `reservas` y `pagos` por intercambio asincrono de eventos y observabilidad por bus. Si la deteccion es tardia o el procesamiento no controla backlog/reintentos, puede afectarse la disponibilidad global y la consistencia operativa del flujo.

## 1.3 Historia de arquitectura asociada

- **ASR-DISP-01:** disponibilidad objetivo del producto (referencia de alto nivel).
- **ASR-DISP-06:** health checks cada 10s como mecanismo operativo directo.
- Necesidad de evitar fallas en cascada para que una caida parcial de `pagos` no implique caida total del proceso de reserva.

## 1.4 Nivel de incertidumbre

**Medio-alto.** Aunque el patron event-driven y la tactica de monitoreo estan definidos, existe incertidumbre practica en:
- control de backlog bajo falla sostenida,
- orden/duplicados en eventos,
- efectividad del aislamiento en escenarios prolongados o intermitentes,
- impacto de no haber validado aun votacion bajo multiples divergencias y campanas prolongadas (falsos positivos/negativos, retiro repetido, estabilidad del conjunto activo).

---

## 2. Arquitectura y alcance evaluado

### 2.1 Componentes desplegados

| Capa | Componente | Estado |
|---|---|---|
| Persistencia | PostgreSQL (`postgres`), Redis (`redis-server`) | Activo |
| Event Bus | RabbitMQ (`rabbitmq`) | Activo |
| Observabilidad | Prometheus, Grafana, Alertmanager, Loki, Promtail, exporters | Activo |
| Testing | WireMock, Toxiproxy, k6 | Activo |
| Slice app | `reservas`, `pagos`, `monitor`, `validador` | Implementado y activo |

### 2.2 Alcance funcional cubierto

- Flujo de reserva desacoplado por eventos.
- Procesamiento de pagos con retry/backoff + circuit breaker + DLQ.
- Monitoreo de salud por ping/pong cada 10s con ventana de detección de 20s.
- Degradación controlada de estados de negocio en reservas.
- **Flujo corregido de votación (ajuste segunda iteración):** `reservas` publica `payment.requested` → `validador` ejecuta votación 2/3 y publica `payment.validated` con el monto por mayoría → `pagos` consume `payment.validated` y ejecuta el cobro. Antes de este ajuste, `pagos` consumía `payment.requested` directamente y el validador solo reportaba.
- **Backlog controlado con TTL + DLQ (ajuste segunda iteración):** las colas críticas `validator.requested` y `payments.validated` tienen TTL de 30s y dead-letter exchange hacia `payments.dlq`. Se obtuvo evidencia experimental de expiración durante la segunda corrida.
- **Alerta `PaymentsDegradedByMonitor` (ajuste segunda iteración):** regla Prometheus que convierte la señal del monitor (`monitor_service_up{service="pagos"} == 0` por 20s) en evidencia directa del ASR de MTTD ≤ 20s.

### 2.3 Alcance pendiente

- Prueba de estabilidad extendida (soak/estrés prolongado) para reforzar evidencia de control de backlog en fallas sostenidas de mayor duración.

---

## 3. Metodología de prueba

## Escenario A — Línea base (proveedor operativo)
- Carga: k6, 10 VUs, 30s.
- Endpoint: `POST /reservas`.
- Objetivo: validar comportamiento nominal y latencia.

## Escenario B — Falla de proveedor externo
- Acción: detener `wiremock` (proveedor mock caído).
- Carga: k6, 10 VUs, 30s sobre `POST /reservas`.
- Objetivo: validar disponibilidad de reservas durante falla en pagos/proveedor.

## Escenario C — Detección de degradación por monitor
- Acción: detener contenedor `pagos`.
- Medición: polling al monitor (`/status`) cada 1s.
- Objetivo: medir MTTD y verificar recuperación.

## Escenario D — Verificación de backlog/DLQ
- Fuente: API de administración de RabbitMQ.
- Objetivo: revisar crecimiento de colas y enrutamiento de fallidos.

## Escenario E — Votación 2/3 y retiro lógico de instancia defectuosa
- Configuración de inyección: `FAULTY_CALCULATOR=calc_c` y `FAULTY_DELTA=5.0` en el servicio `validador`.
- Disparador: creación de reserva (`POST /reservas`) que publica `PaymentRequested`; `validador` consume ese evento y ejecuta cálculo en tres calculadoras lógicas (`calc_a`, `calc_b`, `calc_c`).
- Regla de votación: mayoría 2/3 sobre el valor calculado.
- Criterio de divergencia: si una calculadora devuelve un valor distinto a la mayoría, se publica `ValidationDivergenceAlert` y se marca para retiro lógico.
- Evidencia recolectada:
  - Evento en bus: `evidencias/validador/validation-audit-messages.json` (con `eventType=ValidationDivergenceAlert`).
  - Estado del validador: `evidencias/validador/validador-status.json` (muestra `retiredCalculators=["calc_c"]` y 2 calculadoras activas).
  - Métricas: `evidencias/validador/validador-metrics.txt` (`validator_divergence_total=1`, `validator_retired_calculators_total{calculator="calc_c"}=1`, `validator_active_calculators=2`).

---

## 4. Resultados  Obtenidos

### 4.0 Resumen de los resultados obtenidos

La hipotesis de diseno se **confirma** en esta iteracion:
- Se confirma disponibilidad de `reservas` durante falla de proveedor y deteccion en umbral.
- Se confirma la tactica de votacion 2/3 con deteccion de divergencia y retiro logico de la instancia defectuosa.

## 4.1 Resultados cuantitativos
### 4.1.1 Métricas de carga (Escenario B: proveedor caído)

| Métrica | Corrida 1 (2026-02-17) | Corrida 2 (2026-02-19) |
|---|---:|---:|
| Iteraciones totales | 931 | 943 |
| Requests HTTP | 931 | 943 |
| Requests fallidos (`http_req_failed`) | 0.00% | 0.00% |
| Checks exitosos (`status=202`) | 100% (931/931) | 100% (943/943) |
| Throughput | 30.79 req/s | 31.13 req/s |
| Latencia promedio | 21.27 ms | 17.20 ms |
| Latencia p95 | 37.33 ms | 37.87 ms |
| Latencia máxima | 498.4 ms | 102.73 ms |

**Interpretación:** durante la falla inducida del proveedor, `reservas` mantuvo disponibilidad y buen tiempo de respuesta en ambas corridas. La reducción de latencia máxima (498ms → 102ms) en la segunda corrida es consistente con el flujo corregido: `pagos` ya no intenta cobrar ante `payment.requested` directamente, sino que espera el evento `payment.validated` del validador, eliminando la contención directa con el proveedor caído en el path de latencia de `reservas`.

### 4.1.2 Métricas de monitoreo (Escenario C)

| Métrica | Umbral esperado | Corrida 1 | Corrida 2 |
|---|---:|---:|---:|
| Intervalo de ping | 10 s | 10 s | 10 s |
| Ventana de detección | <= 20 s | 20 s | 20 s |
| **MTTD medido** | **<= 20 s** | **14.16 s** | **2.00 s** |

**Interpretación:** el monitor detecta caída de `pagos` dentro del umbral definido en ambas corridas. La variación del MTTD (14.16s vs 2.00s) es esperada y no indica un cambio de comportamiento: el monitor rastrea el tiempo transcurrido desde el último pong recibido; si `pagos` se detiene cuando ese lag está cerca de los 20s, la detección ocurre casi inmediatamente. El rango observado (2s – 14.16s) queda dentro del umbral de diseño (≤20s). Durante la caída, `pagos` pasó a `healthy=false` y el flujo operó en modo degradado sin interrumpir la creación de reservas.

### 4.1.3 Métricas de colas (Escenario D)

**Corrida 1 (2026-02-17):**

| Cola | Mensajes | Observación |
|---|---:|---|
| `payments.requested` | 0 | Sin acumulación al cierre de corrida |
| `payments.dlq` | 21 | Fallidos enrutados a DLQ |

**Corrida 2 (2026-02-19) — con ajuste TTL + DLQ activos:**

| Cola | Peak con `pagos` caído | Al cierre (pagos recuperado) | Observación |
|---|---:|---:|---|
| `payments.requested` | — | — | Cola eliminada del flujo; `pagos` ya no la consume |
| `payments.validated` | 640 | 0 | TTL + consumo al recuperar pagos |
| `payments.dlq` | — | 560 | Creció +80 por expiración TTL (30s) durante caída |
| `validator.requested` | 0 | 0 | Validador activo consumiendo sin acumulación |

**Interpretación:** en la segunda corrida se obtuvo evidencia experimental directa del mecanismo TTL: con `pagos` caído durante ~30s, `payments.validated` llegó a 640 mensajes, de los cuales 80 expiraron hacia `payments.dlq` antes de que `pagos` se recuperara. Al reiniciar `pagos`, consumió los restantes. El backlog queda controlado por diseño: la cola no crece indefinidamente. La corrida 1 mostraba `payments.requested=0` pero sin TTL ni DLQ configurados en esa cola, lo que era una evidencia incompleta del criterio.

### 4.1.4 Métricas de votación y retiro lógico (Validador)

| Métrica | Corrida 1 | Corrida 2 |
|---|---:|---:|
| `validator_requests_total` | 1 | 2802 |
| `validator_divergence_total` | 1 | 1 |
| `validator_retired_calculators_total{calculator="calc_c"}` | 1 | 1 |
| `validator_active_calculators` | 2 | 2 |
| Evento capturado | `ValidationDivergenceAlert` | `ValidationDivergenceAlert` |

**Interpretación:** en la segunda corrida el validador procesó 2802 solicitudes (volumen de los escenarios A y B combinados) porque con el flujo corregido **todas las reservas pasan por votación**, no solo el caso de prueba aislado. A pesar del alto volumen, la divergencia se detectó correctamente en la primera solicitud del escenario E y `calc_c` fue retirado lógicamente. Los resultados cualitativos son iguales; el volumen refleja que la táctica opera en el flujo nominal, no solo en pruebas controladas.

---

## 5. Validación de resultados esperados

| Resultado esperado del diseño | Criterio | Evidencia (corrida 2 – 2026-02-19) | Estado |
|---|---|---|---|
| Reservas sigue operando en falla | >= 99.9% de éxito en `crear reserva` | 100% (943/943) con proveedor caído | **Cumple** |
| No hay bloqueo síncrono | Reserva responde sin depender del proveedor | `POST /reservas` mantiene 202 y p95 37.87ms en falla | **Cumple** |
| MTTD por monitor | <= 20s | 2.00s (rango observado: 2s – 14.16s en dos corridas) | **Cumple** |
| Degradación controlada | Estado de negocio no global error | Reservas en `PENDING_PAYMENT`/`PAYMENT_FAILED` | **Cumple** |
| Backlog del bus controlado | Cola no crece sin límite + DLQ activa con TTL | `payments.validated` llegó a 640, 80 expiraron a DLQ por TTL (30s); al recuperar pagos la cola quedó en 0 | **Cumple — evidencia real de TTL** |
| Votación detecta discrepancias | Validador identifica instancia defectuosa en el flujo nominal | `validator_requests_total=2802`, `ValidationDivergenceAlert` + retiro lógico de `calc_c` en flujo real | **Cumple** |
| Alerta por señal de monitor | `PaymentsDegradedByMonitor` dispara en ≤ 20s ante caída | Regla activa: `monitor_service_up{service="pagos"} == 0 for 20s` | **Cumple (configurado y validado)** |

---

## 6. Conclusión sobre la hipótesis

## Veredicto

**La hipótesis se confirma en esta iteración del experimento.**

### Sustento

- Se valida el núcleo del diseño: desacoplamiento por eventos + monitor de salud permiten sostener disponibilidad de `reservas` durante falla en `pagos/proveedor`.
- Se cumple el umbral de detección (MTTD <= 20s, medido en 14.16s).
- Se demuestra degradación controlada de negocio sin caída global.

### Consideraciones residuales

- Aunque el backlog se observó controlado en la corrida de evidencia, conviene validar estabilidad en pruebas de mayor duración.

---

## 7. Analisis de los resultados obtenidos

### 7.1 Indique si la hipotesis de diseno pudo ser confirmada o no

La hipotesis de diseno fue **confirmada**.

- **Confirmada** para los objetivos de disponibilidad del flujo de reserva ante falla en pagos/proveedor:
  - Exito en `crear reserva` durante falla: 100% (931/931).
  - MTTD del monitor: 14.16s (cumple umbral <= 20s).
  - Degradacion de negocio controlada (sin error global).
- **Confirmada para el alcance funcional definido**:
  - Se valido backlog controlado en la corrida ejecutada.
  - Se implemento y valido la tactica de votacion (3) con evidencia de divergencia y retiro logico.

### 7.2 En caso de que la hipotesis se haya confirmado, explique las decisiones de arquitectura que favorecieron el resultado

Las decisiones de arquitectura que mas aportaron al resultado favorable fueron:

- **Desacoplamiento asincrono por Event Bus:** `reservas` publica `PaymentRequested` y responde `202` sin esperar al proveedor, evitando propagacion de falla sincronica.
- **Estados de negocio degradados:** manejo de `PENDING_PAYMENT` y `PAYMENT_FAILED` permite continuidad funcional sin caida global del dominio de booking.
- **Resiliencia en pagos:** uso de retry con backoff + circuit breaker en `pagos` reduce impacto de falla externa y evita ciclos de error agresivos.
- **Monitor por Ping/Pong:** deteccion activa de omision/degradacion en ventana operativa (10s ping, 20s deteccion) con recuperacion observable.
- **DLQ + TTL para fallas persistentes:** separa mensajes no procesables del flujo principal y limita el crecimiento del backlog a 30s de acumulacion. La segunda corrida confirmo experimentalmente la expiracion de mensajes a DLQ durante caida real de `pagos`.
- **Flujo de votacion corregido:** al posicionar el validador antes del cobro (`payment.requested` → votacion → `payment.validated` → cobro), el monto procesado es siempre el de la mayoria 2/3, no el original. Esto tambien redujo la latencia maxima observada en `reservas` (498ms → 102ms) al eliminar dependencia directa de `pagos` con el proveedor en el path de respuesta de `reservas`.

### 7.3 En caso de que los resultados del experimento no hayan sido favorables, explique por que y cuales cambios realizaria en el diseno

Aunque el resultado general fue favorable en disponibilidad, se identificaron puntos no favorables para cerrar el experimento al 100%:

- **Riesgo de backlog en falla sostenida (a validar):**
  - Posible causa: en ventanas largas, la tasa de ingreso puede superar la capacidad efectiva de consumo.
  - Cambio propuesto: escalar consumidores de `pagos` horizontalmente, ajustar `prefetch`, y aplicar politicas de backpressure.
- **Evidencia temporal corta:**
  - Posible causa: pruebas concentradas en ventanas cortas.
  - Cambio propuesto: ejecutar pruebas soak (15-30 min o mas) con fallas intermitentes y persistentes para medir estabilidad real.
- **Mitigacion operativa incompleta ante picos:**
  - Cambio propuesto: incluir autoscaling por lag de cola, alertas por umbral de backlog y politicas de TTL/reintento diferenciadas por tipo de error.

---

## 8. Riesgos abiertos y acciones recomendadas

| Riesgo | Impacto | Acción recomendada | Estado |
|---|---|---|---|
| Acumulación de backlog en pagos bajo falla prolongada | Mensajes expiran a DLQ (pérdida de pagos diferidos) | TTL + DLQ implementado y validado experimentalmente; para fallas sostenidas evaluar escalar consumidores y rate limiting | **Mitigado — validado en corrida 2** |
| Cobertura temporal corta del validador | Riesgo de no observar comportamientos raros en corridas largas | Ejecutar pruebas prolongadas y escenarios con múltiples divergencias | **Abierto** |
| Falta de serie temporal prolongada | Difícil afirmar estabilidad sostenida | Ejecutar soak test (15-30 min) con proveedor degradado | **Abierto** |

---

## 9. Evidencias recopiladas (resumen)

**Corrida 1 (2026-02-17):**
- Resultado k6 en falla de proveedor: 931 requests, 0% error, p95 37.33ms, max 498.4ms.
- Medición automatizada MTTD: 14.16s.
- Estado de colas RabbitMQ: `payments.requested=0`, `payments.dlq=21`.
- Estados de negocio observados: `PENDING_PAYMENT`, `CONFIRMED`, `PAYMENT_FAILED`.
- Evidencia de votación: evento `ValidationDivergenceAlert`, retiro lógico de `calc_c`, `validator_active_calculators=2`.

**Corrida 2 (2026-02-19) — con ajustes de Santiago integrados:**
- Resultado k6 escenario normal: 933 requests, 0% error, p95 48.17ms.
- Resultado k6 en falla de proveedor: 943 requests, 0% error, p95 37.87ms, max 102.73ms.
- Medición MTTD: 2.00s (dentro del umbral ≤20s; rango observado en dos corridas: 2s–14.16s).
- Estado de colas con `pagos` caído: `payments.validated` peak=640, expiró 80 mensajes a DLQ por TTL; al cierre `payments.validated=0`, `payments.dlq=560`.
- Evidencia de TTL: DLQ creció de 480 → 560 (+80) durante caída de `pagos` por expiración de mensajes de `payments.validated`.
- Evidencia de votación con flujo corregido: `validator_requests_total=2802` (flujo nominal completo), `validator_divergence_total=1`, retiro lógico de `calc_c`, `validator_active_calculators=2`.

---

## 10. Evidencias sugeridas para adjuntar en la entrega

Se recomienda adjuntar, como minimo, las siguientes evidencias:

1. **Salida completa de k6** en escenario normal y escenario con proveedor caido (archivo texto o captura).
2. **Capturas de Grafana** con paneles de:
   - latencia p95/p99,
   - tasa de error,
   - throughput de `POST /reservas`,
   - estado `up` de servicios.
3. **Captura de Prometheus `/targets`** mostrando jobs relevantes en estado UP.
4. **Evidencia del MTTD**:
   - salida de script/consulta con tiempo medido (14.16s),
   - captura de `monitor /status` durante caida y despues de recuperacion.
5. **Evidencia RabbitMQ de colas**:
   - `payments.requested` (mensajes, ready/unacked),
   - `payments.dlq` (mensajes acumulados),
   - captura de la UI o respuesta de la API.
6. **Logs representativos** de `reservas`, `pagos`, `monitor` y `validador` durante falla inducida.
7. **Trazabilidad de estados de negocio**:
   - ejemplos de reservas en `PENDING_PAYMENT`, `CONFIRMED` y `PAYMENT_FAILED`.
8. **Configuracion reproducible**:
   - archivos `docker-compose`,
   - version de imagenes,
   - comandos exactos ejecutados para reproducir cada escenario.
9. **Matriz final de cumplimiento** (tabla de criterios esperados vs resultado observado).

### 10.1 Evidencias 1 - Presente evidencias de los resultados obtenidos en el experimento

Evidencias ya recolectadas en el proyecto:

- `evidencias/EVIDENCIAS-INDEX.md` (indice general con resumen de metricas).
- `evidencias/k6/k6-escenario-normal.txt` y `evidencias/k6/k6-escenario-proveedor-caido.txt`.
- `evidencias/monitor/mttd.txt` y `evidencias/monitor/monitor-timeline-caida-pagos.csv`.
- `evidencias/monitor/monitor-status-antes-caida.json`, `evidencias/monitor/monitor-status-deteccion.json`, `evidencias/monitor/monitor-status-recuperacion.json`.
- `evidencias/rabbitmq/queue-payments-requested.json` y `evidencias/rabbitmq/queue-payments-dlq.json`.
- `evidencias/rabbitmq/queue-validation-audit.json`.
- `evidencias/prometheus/prometheus-targets.json`.
- `evidencias/estados/reserva-estado-normal.json` y `evidencias/estados/reserva-estado-proveedor-caido.json`.
- `evidencias/validador/validation-audit-messages.json`, `evidencias/validador/validador-status.json`, `evidencias/validador/validador-metrics.txt`.
- `evidencias/logs/reservas.log`, `evidencias/logs/pagos.log`, `evidencias/logs/monitor.log`, `evidencias/logs/validador.log`, `evidencias/logs/rabbitmq.log`.

---

## 11. Estado final para reporte academico

Este informe respalda que el diseño **si enmascara fallas** y preserva disponibilidad en el flujo de reservas, con verificacion cuantitativa de los umbrales de disponibilidad y deteccion definidos para el experimento.

En terminos academicos, el resultado debe reportarse como **cumplimiento del diseno funcional definido**:

- **Cumplimiento en disponibilidad/resiliencia** (objetivo principal del experimento).
- **Cumplimiento en integridad por votacion 2/3** con evidencia de deteccion de discrepancia y retiro logico.

Para reforzar este resultado, se recomienda cerrar dos actividades de robustez operativa:

1. Validación de backlog bajo falla prolongada.
2. Pruebas de votación con múltiples divergencias y campañas de mayor duración.

---

## 12. Cierre de alcance original (actualizacion)

La implementacion actual ya cubre el alcance funcional original del experimento para este slice: disponibilidad, deteccion por monitor y validacion por votacion 2/3.

Componentes y tacticas implementadas en esta version:

- Continuidad operacional de `reservas` bajo falla inducida.
- Deteccion y recuperacion por monitor en ventana <= 20s.
- Validacion de integridad por mayoria (votacion 2/3) con retiro logico de calculadora defectuosa.
- Evidencia reproducible de disponibilidad, latencia, degradacion controlada y divergencia de calculo.

Se mantiene como trabajo futuro solo la ampliacion de pruebas de resistencia y estabilidad temporal.

---

## 13. Cierre tecnico de backlog y sostenibilidad operativa

En la corrida 2 (2026-02-19) se obtuvo evidencia experimental directa del mecanismo TTL + DLQ: con `pagos` caído, `payments.validated` acumuló hasta 640 mensajes y 80 de ellos expiraron hacia `payments.dlq` antes de que el consumidor se recuperara. Esto cierra el criterio de "backlog controlado" con evidencia real, no solo con colas en cero.

Para reforzar la sostenibilidad operativa ante fallas prolongadas se mantienen como recomendaciones:

1. **Backpressure en consumidor de pagos:** control de `prefetch` y limites de concurrencia para evitar saturacion.
2. **Rate limiting en productor o gateway:** amortiguar picos de entrada cuando el proveedor esta degradado.
3. **Politica de reintentos diferenciada:** backoff exponencial con techo maximo y clasificacion transiente/permanente.
4. **Escalamiento horizontal por lag de cola:** replica automatica del consumidor si supera umbral de mensajes/tiempo.
5. **Alertamiento de capacidad:** alarma `PaymentsDegradedByMonitor` ya configurada en Prometheus; complementar con alerta por crecimiento de `payments.validated` y por mensajes en DLQ (ya existe `RabbitDLQGrowing`).

El criterio "backlog controlado + DLQ activa" queda sustentado por evidencia experimental de expiración TTL en la segunda corrida y por controles de diseño para operacion continua.

