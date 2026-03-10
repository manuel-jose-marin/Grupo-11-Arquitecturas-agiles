from common import build_base_payload, send_payload, sign_payload

payload = sign_payload(build_base_payload())
status, body = send_payload(payload)
print(f"status={status}")
print(body)
print(f"requestId={payload['meta']['requestId']}")
