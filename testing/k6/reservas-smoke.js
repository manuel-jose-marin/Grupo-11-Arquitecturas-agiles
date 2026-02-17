import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 10,
  duration: "30s",
  thresholds: {
    http_req_failed: ["rate<0.01"],
    http_req_duration: ["p(95)<500"],
  },
};

export default function () {
  const payload = JSON.stringify({
    userId: `user-${__VU}-${__ITER}`,
    amount: 120.5,
  });

  const params = {
    headers: { "Content-Type": "application/json" },
  };

  const res = http.post("http://reservas:8080/reservas", payload, params);
  check(res, {
    "status is 202": (r) => r.status === 202,
  });
  sleep(0.3);
}
