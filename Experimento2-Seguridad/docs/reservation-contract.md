# Contrato de la solicitud de reserva - Experimento Seguridad

## Endpoint de entrada

`POST /api/v1/reservations`

## Estructura del mensaje

```json
{
  "actor": {
    "userId": "user-001",
    "role": "traveler"
  },
  "reservation": {
    "hotelId": "HTL-BOG-001",
    "roomType": "DELUXE",
    "checkIn": "2026-03-20",
    "checkOut": "2026-03-23",
    "guests": 2,
    "currency": "COP",
    "totalAmount": 850000,
    "cancellationPolicyId": "POL-FLEX-24H",
    "quoteId": "QUOTE-2026-0001",
    "quoteHash": "sha256(actor+campos_criticos)"
  },
  "meta": {
    "requestId": "uuid-v4",
    "nonce": "uuid-v4",
    "timestamp": "2026-03-10T22:00:00Z",
    "sig": "hmac-sha256-hex"
  }
}
```

## Campos críticos protegidos

- `hotelId`
- `roomType`
- `checkIn`
- `checkOut`
- `guests`
- `currency`
- `totalAmount`
- `cancellationPolicyId`
- `quoteId`

## Canonicalización usada en el experimento

### 1. `quoteHash`

Se calcula con SHA-256 sobre:

```text
userId|role|hotelId|roomType|checkIn|checkOut|guests|currency|totalAmount|cancellationPolicyId|quoteId
```

### 2. `sig`

Se calcula con HMAC-SHA256 usando la llave compartida `super-secret-exp2-key` sobre:

```text
requestId|nonce|timestamp|userId|role|hotelId|roomType|checkIn|checkOut|guests|currency|totalAmount|cancellationPolicyId|quoteId|quoteHash
```

## Reglas del verificador

- Si `quoteHash` no coincide con el payload recibido → `INTEGRITY_VIOLATION`
- Si `sig` no coincide → `INTEGRITY_VIOLATION`
- Si `nonce` ya fue usado dentro de la ventana TTL → `REPLAY_DETECTED`
- Si `timestamp` está fuera de la ventana permitida → rechazo
- Solo si todo es válido, la petición continúa hacia `reservations`

## Valores operativos del experimento

- Ventana TTL para `nonce`: `120` segundos
- Desfase máximo permitido del reloj (`timestamp`): `120` segundos
- Respuesta esperada:
  - solicitud válida: `201`
  - tampering: `422`
  - replay: `409`
