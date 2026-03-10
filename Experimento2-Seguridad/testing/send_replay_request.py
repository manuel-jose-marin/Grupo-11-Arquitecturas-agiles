from common import build_base_payload, send_payload, sign_payload

payload = sign_payload(build_base_payload())
first_status, first_body = send_payload(payload)
second_status, second_body = send_payload(payload)
print("first_request")
print(f"status={first_status}")
print(first_body)
print("second_request")
print(f"status={second_status}")
print(second_body)
print(f"requestId={payload['meta']['requestId']}")
