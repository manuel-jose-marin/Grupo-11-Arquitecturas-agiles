# Informe de resultados — Experimento 2 (Seguridad)

**Proyecto:** TravelHub — Experimento 2  
**Fecha de ejecucion (plan):** 2026-03-10  
**Fecha de corrida de evidencias:** 2026-03-10  
**Entorno:** Docker Compose local (macOS)  
**Autores:** Integrantes del Grupo 11 - Arquitecturas Ágiles

---

## Titulo del experimento

Deteccion de alteracion y replay de parametros criticos de reserva en transito (Cliente - Reservas).

---

## 1. Proposito del experimento

Construir y ejecutar un sistema que materialice el flujo **Cliente → Gateway/BFF → Verificador de Integridad y Anti-Replay → Servicio de Reservas**, con un *Audit Log* dedicado, para inducir de forma controlada ataques de **tampering** y **replay** sobre parámetros críticos de una reserva y validar que el diseño detecta el evento antes de que el servicio acepte o procese la solicitud.

La respuesta de rechazo es solo una consecuencia operativa; la evidencia principal reside en el **registro verificable** de cada evento en el *Audit Log*. Con este montaje se busca demostrar, mediante evidencia cuantificable, que el sistema detecta:

- solicitudes con parámetros críticos **adulterados** (tarifa, fechas, tipo de habitación o política de cancelación), y
- solicitudes **reenviadas** (replay) por un atacante que reutiliza una petición previamente válida.

---

## 1.1 Hipotesis de diseño asociada al experimento

Si el sistema emite y exige un sello de integridad para los parametros criticos de la reserva, calculado sobre un payload canonico e incluyendo `nonce` y `timestamp`, y ademas el Verificador de Integridad y Anti-Replay valida obligatoriamente ese sello antes de permitir que la solicitud llegue al Servicio de Reservas, entonces cualquier solicitud con parametros adulterados o reenviada sera detectada de forma consistente y no podra procesarse como valida; adicionalmente, cada deteccion quedara registrada como evidencia verificable en el `Audit Log`.

## 1.2 Punto de sensibilidad

El punto de sensibilidad del experimento es el servicio `verifier`, particularmente en:

- la validacion del `quoteHash` calculado sobre el payload canonico;
- la validacion del sello `sig` mediante HMAC-SHA256;
- la verificacion de `nonce` con ventana TTL;
- la validacion de `timestamp` dentro del skew permitido;
- la generacion de evidencia de auditoria y el corte temprano del flujo.

## 1.3 Historia de arquitectura asociada

- **ASR-SEC-03:** integridad de parametros de reserva en transito.
- La validacion debe ejecutarse sobre el `100%` de las solicitudes antes de persistir o aplicar cambios.
- La evidencia principal del experimento debe quedar registrada en auditoria con trazabilidad suficiente para inspeccion posterior.

## 1.4 Nivel de incertidumbre

**Medio.** La efectividad del experimento depende de decisiones de implementacion como:

- la canonicalizacion exacta del payload;
- la definicion precisa de los campos criticos protegidos;
- la configuracion de la ventana de tiempo (`timestamp`);
- la gestion del registro de `nonce` dentro de la ventana TTL;
- la correcta propagacion de `requestId` e IP hacia auditoria.

---

## 2. Arquitectura y alcance evaluado

### 2.1 Componentes desplegados

| Capa | Componente | Estado |
|---|---|---|
| Entrada | `gateway` | Activo |
| Seguridad transversal | `verifier` | Activo |
| Dominio | `reservations` | Activo |
| Evidencia trazable | `auditlog` | Activo |
| Persistencia auditoria | MongoDB (`exp2-mongodb`) | Activo |
| Observabilidad | Prometheus, Grafana, Alertmanager, Loki, Promtail | Activo |

### 2.2 Alcance funcional cubierto

- Validacion obligatoria de integridad sobre `quoteHash`.
- Validacion obligatoria de autenticidad del mensaje sobre `sig`.
- Deteccion de replay mediante `nonce` reutilizado dentro de una ventana TTL de `120s`.
- Rechazo de solicitudes con `timestamp` fuera de la ventana permitida (`120s`).
- Propagacion de `requestId` extremo a extremo.
- Persistencia de reservas validas en SQLite.
- Registro de eventos `REQUEST_ACCEPTED`, `INTEGRITY_VIOLATION` y `REPLAY_DETECTED` en `Audit Log`.
- Consulta verificable de auditoria por `requestId`.
- Exposicion de metricas Prometheus para los cuatro servicios del slice.

---

## 3. Metodologia de prueba

La corrida de evidencias se ejecuto con **20 repeticiones por escenario**, generando `requestId` y `nonce` unicos por solicitud.

## Escenario A — Solicitud valida (control)

- Entrada: payload firmado correctamente con `quoteHash` y `sig`.
- Objetivo: verificar aceptacion del flujo nominal y ausencia de falsos positivos.
- Criterios:
  - respuesta `201`;
  - reserva consultable por `requestId`;
  - evidencia `REQUEST_ACCEPTED` en `Audit Log`.

## Escenario B — Tampering de parametros criticos

- Entrada: payload inicialmente firmado, luego adulterado en `reservation.totalAmount`.
- Objetivo: verificar deteccion de alteracion y no procesamiento.
- Criterios:
  - respuesta `422`;
  - consulta de reserva por `requestId` devuelve `404`;
  - evidencia `INTEGRITY_VIOLATION` en `Audit Log`.

## Escenario C — Replay

- Entrada: mismo payload valido reenviado dos veces con igual `requestId`, `nonce`, `timestamp`, `quoteHash` y `sig`.
- Objetivo: verificar bloqueo del segundo intento sin crear una segunda reserva.
- Criterios:
  - primer envio `201`;
  - segundo envio `409`;
  - existe una sola reserva efectiva;
  - `Audit Log` contiene `REQUEST_ACCEPTED` y `REPLAY_DETECTED`.

## Escenario D — Observabilidad y salud

- Fuente: endpoints `/health`, `/metrics` y API `/api/v1/targets` de Prometheus.
- Objetivo: validar que los servicios del experimento quedan visibles y monitoreables.
- Criterios:
  - `gateway`, `verifier`, `reservations` y `auditlog` en estado `up`;
  - metricas relevantes accesibles;
  - evidencias disponibles en la carpeta `evidencias/`.

---

## 4. Resultados obtenidos

### 4.0 Resumen de los resultados obtenidos

La hipotesis de diseño se **confirma** en esta iteracion:

- se detecto `100%` de los casos de tampering;
- se detecto `100%` de los casos de replay;
- no hubo falsos positivos sobre solicitudes validas en la corrida cuantitativa;
- se verifico el **no procesamiento** para tampering y el **no doble procesamiento** para replay;
- se obtuvo evidencia trazable en `Audit Log` para cada solicitud observada.

## 4.1 Resultados cuantitativos

### 4.1.1 Escenario A — Solicitudes validas

| Metrica | Resultado |
|---|---:|
| Solicitudes validas ejecutadas | 20 |
| Respuestas `201` | 20 |
| Reservas consultables (`200`) | 20 |
| Evidencias `REQUEST_ACCEPTED` presentes | 20 |
| Tasa de aceptacion | 100.0% |
| Tasa de falsos positivos | 0.0% |

**Interpretacion:** el flujo control se proceso sin rechazos espurios. Todas las solicitudes validas llegaron a `reservations`, fueron persistidas y dejaron evidencia verificable en `Audit Log`.

### 4.1.2 Escenario B — Tampering

| Metrica | Resultado |
|---|---:|
| Solicitudes adulteradas ejecutadas | 20 |
| Respuestas `422` | 20 |
| Solicitudes no procesadas (`404` al consultar) | 20 |
| Evidencias `INTEGRITY_VIOLATION` presentes | 20 |
| Tasa de deteccion | 100.0% |

**Interpretacion:** la alteracion del parametro critico `totalAmount` fue detectada de forma consistente antes de llegar a `reservations`. El sistema no confirmo ni aplico cambios para ninguna solicitud adulterada.

### 4.1.3 Escenario C — Replay

| Metrica | Resultado |
|---|---:|
| Escenarios replay ejecutados | 20 |
| Primer envio aceptado (`201`) | 20 |
| Segundo envio rechazado (`409`) | 20 |
| Reservas efectivas consultables (`200`) | 20 |
| Solicitudes con doble evidencia (`REQUEST_ACCEPTED` + `REPLAY_DETECTED`) | 20 |
| Tasa de deteccion del segundo envio | 100.0% |

**Interpretacion:** el primer envio valido fue aceptado, pero el reenvio del mismo payload fue bloqueado por reutilizacion de `nonce`. La consulta final por `requestId` mostro una sola reserva efectiva, por lo que no hubo duplicacion de efectos.

### 4.1.4 Observabilidad y metricas

| Metrica / evidencia | Resultado |
|---|---:|
| Targets relevantes de Prometheus en `up` | 4/4 |
| `gateway_requests_total{outcome="201",route="create_reservation"}` | 43 |
| `verifier_integrity_violation_total{reason="quote_hash_mismatch"}` | 22 |
| `verifier_replay_detected_total{reason="nonce_reused"}` | 21 |
| `reservations_created_total` | 43 |
| `audit_events_persisted_total{event_type="REQUEST_ACCEPTED"}` | 43 |
| `audit_events_persisted_total{event_type="INTEGRITY_VIOLATION"}` | 22 |
| `audit_events_persisted_total{event_type="REPLAY_DETECTED"}` | 21 |

**Interpretacion:** Prometheus observo correctamente los cuatro servicios del experimento. Las metricas acumulativas incluyen la validacion de humo ejecutada antes de la corrida cuantitativa, por lo que se usan como evidencia complementaria de observabilidad y no como fuente primaria de la tasa de deteccion.

---

## 5. Validacion de resultados esperados

| Resultado esperado del diseño | Criterio | Evidencia (corrida 2026-03-10) | Estado |
|---|---|---|---|
| Tampering detectado | 100% de solicitudes adulteradas rechazadas | 20/20 respuestas `422` y 20/20 eventos `INTEGRITY_VIOLATION` | **Cumple** |
| Replay detectado | 100% del segundo envio rechazado | 20/20 segundos envios en `409` y 20/20 eventos `REPLAY_DETECTED` | **Cumple** |
| Cero o casi cero falsos positivos | Solicitudes validas no rechazadas | 20/20 solicitudes validas aceptadas (`201`) | **Cumple** |
| No procesamiento ante violacion | Solicitud adulterada no llega a reserva persistida | 20/20 consultas por tampering en `404` | **Cumple** |
| No doble procesamiento ante replay | Solo una reserva efectiva por solicitud reenviada | 20/20 escenarios replay con una sola reserva consultable | **Cumple** |
| Evidencia verificable en auditoria | Cada solicitud relevante deja trazabilidad en `Audit Log` | Eventos con `requestId`, actor, IP, `timestamp`, `quoteId`, `payloadHash` y `reason` | **Cumple** |
| Observabilidad del slice | Servicios visibles en Prometheus | 4/4 targets (`gateway`, `verifier`, `reservations`, `auditlog`) en `up` | **Cumple** |

---

## 6. Conclusion sobre la hipotesis

### Veredicto

**La hipotesis de diseño se confirma en esta iteracion del experimento.**

### Sustento

- El verificador detecto consistentemente alteraciones del payload antes de permitir el procesamiento.
- El mecanismo `nonce + timestamp + TTL` detecto de forma consistente el replay del segundo envio.
- `reservations` solo proceso solicitudes previamente validadas y no acepto trafico adulterado.
- El `Audit Log` consolido la evidencia principal exigida por el diseño y permitio verificar cada resultado por `requestId`.

### Consideraciones residuales

- La estrategia de `nonce` en memoria resulta suficiente para el alcance de esta corrida local, aunque sigue siendo pertinente contrastarla en un escenario distribuido con multiples replicas.
- La evidencia obtenida valida el comportamiento funcional esperado; sin embargo, una campaña mas extensa permitiria ampliar el sustento sobre robustez operacional y tolerancia a desviaciones temporales.

---

## 7. Analisis de los resultados obtenidos

### 7.1 Indique si la hipotesis de diseño pudo ser confirmada o no

La hipotesis de diseño fue **confirmada**.

- **Confirmada** para integridad de parametros en transito:
  - 20/20 casos de tampering detectados;
  - 20/20 solicitudes adulteradas no procesadas.
- **Confirmada** para anti-replay:
  - 20/20 segundos envios detectados y bloqueados;
  - 20/20 escenarios replay sin efecto duplicado en reservas.
- **Confirmada** para el flujo control:
  - 20/20 solicitudes validas aceptadas;
  - 0 falsos positivos en la corrida de evidencia.

### 7.2 En caso de que la hipotesis se haya confirmado, explique las decisiones de arquitectura que favorecieron el resultado

Las decisiones de arquitectura que mas favorecieron el resultado fueron:

- **Verificador dedicado:** concentra la logica de integridad y anti-replay antes del ingreso al dominio.
- **Payload canonico:** evita ambiguedades al calcular `quoteHash` y `sig`.
- **HMAC-SHA256 sobre mensaje completo:** protege integridad y autenticidad del contenido y de sus metadatos.
- **Nonce + timestamp + TTL:** materializa una defensa explicita contra reenvios.
- **Fail-safe default:** cualquier solicitud que no pueda verificarse se rechaza antes de tocar `reservations`.
- **Audit Log separado:** la evidencia no depende de inspeccion manual de logs del servicio, sino de un registro consultable y trazable.
- **Validacion de cabecera en `reservations`:** el dominio no acepta trafico que no venga marcado como verificado.

### 7.3 Trabajo futuro derivado de los resultados obtenidos

Aunque los resultados fueron favorables para el alcance definido, del analisis realizado se desprenden las siguientes lineas de continuidad:

- **Persistencia distribuida de nonces:** migrar el registro de `nonce` a un almacenamiento compartido para eliminar vacios potenciales en despliegues multi-instancia.
- **Cobertura de errores de reloj:** incorporar pruebas con `timestamp` fuera de ventana y con relojes deliberadamente desalineados.
- **Campanas de repeticion mas largas:** extender la duracion de las corridas para observar el comportamiento estable del verificador y del `Audit Log`.

---

## 8. Riesgos abiertos y trabajo futuro

| Riesgo | Impacto | Linea de trabajo | Estado |
|---|---|---|---|
| Registro de `nonce` solo en memoria | Puede no escalar bien a multiples replicas | Migrar el registro anti-replay a un almacenamiento compartido con TTL | **Abierto** |
| Validacion temporal solo en ventanas cortas | Riesgo de no observar efectos de clocks degradados | Ejecutar pruebas con skew forzado y latencia artificial | **Abierto** |
| Cobertura temporal corta de la corrida | Limitada evidencia sobre estabilidad prolongada | Ejecutar campañas de 15-30 min con volumen sostenido | **Abierto** |

---

## 9. Evidencias recopiladas (resumen)

- `evidencias/escenarios/corrida-seguridad-resumen.json`: resumen cuantitativo de 20 repeticiones por escenario.
- `evidencias/estados/reserva-valida.json`: evidencia de reserva persistida correctamente.
- `evidencias/estados/reserva-tampering-no-procesada.json`: evidencia de no procesamiento.
- `evidencias/estados/reserva-replay-estado-final.json`: evidencia de una sola reserva efectiva tras replay.
- `evidencias/auditlog/valid-request-logs.json`: evento `REQUEST_ACCEPTED`.
- `evidencias/auditlog/tampering-logs.json`: evento `INTEGRITY_VIOLATION`.
- `evidencias/auditlog/replay-logs.json`: secuencia `REQUEST_ACCEPTED` + `REPLAY_DETECTED`.
- `evidencias/prometheus/prometheus-targets.json`: verificacion de observabilidad.
- `evidencias/metrics/*.txt`: metricas expuestas por los servicios.

---

## 10. Evidencias ya recolectadas en el proyecto

Evidencias disponibles en esta version:

- `evidencias/EVIDENCIAS-INDEX.md`
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

---

## 11. Cierre academico del experimento

Con base en la implementacion realizada y en la evidencia recolectada, concluimos que el diseño **detecta alteracion y replay** sobre parametros criticos de reserva antes del procesamiento, y que dicha deteccion queda respaldada por evidencia cuantitativa y trazabilidad verificable.

En consecuencia, para el alcance evaluado en esta iteracion, el experimento demuestra:

- **Cumplimiento de ASR-SEC-03** en el slice validado.
- **Cumplimiento del criterio de no procesamiento** ante violacion de integridad.
- **Cumplimiento del criterio de evidencia verificable** mediante `Audit Log`.

Como continuacion natural del trabajo realizado, identificamos:

1. el fortalecimiento del almacenamiento de `nonce` para escenarios distribuidos;
2. la ampliacion de las campañas de prueba con mayor duracion y skew inducido;
3. la incorporacion de capturas de dashboards y video de corrida final como soporte audiovisual de la entrega.
