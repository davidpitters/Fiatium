"""Real HTTP smoke. Use --direct only for the explicitly broker-free local demo."""

import argparse
import time
from uuid import uuid4

import httpx
from fiatium.config import settings
from fiatium.worker import local_once

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8000")
parser.add_argument("--direct", action="store_true")
args = parser.parse_args()
token = next(key for key, value in settings().tokens.items() if value["role"] == "operator")
with httpx.Client(
    base_url=args.url, headers={"Authorization": f"Bearer {token}"}, timeout=30
) as client:

    def post(path, body, **kwargs):
        response = client.post(path, json=body, **kwargs)
        response.raise_for_status()
        return response.json()

    client.get("/health/ready").raise_for_status()
    customer = post("/api/customers", {"name": "HTTP smoke (simulated)"})
    invoice = post("/api/invoices", {"customer_id": customer["id"], "amount": 4200})
    payload = {"invoice_id": invoice["id"], "amount": 4200, "scenario": "success"}
    headers = {"Idempotency-Key": str(uuid4())}
    payment = post("/api/payments", payload, headers=headers)
    assert post("/api/payments", payload, headers=headers) == payment
    if args.direct:
        local_once()
    for _ in range(60):
        detail = client.get(f"/api/payments/{payment['id']}").json()
        if detail["status"] == "settled":
            break
        time.sleep(1)
    assert detail["status"] == "settled", detail["status"]
    lines = [
        j
        for j in client.get("/api/journals").json()
        if j["operation"] == f"settlement:{payment['id']}"
    ]
    assert len(lines) == 2 and sum(j["amount"] for j in lines) == 0
    result = post("/api/reconciliation-runs", {})
    assert not [f for f in result["findings"] if f["payment_id"] == payment["id"]]
    print(f"HTTP smoke passed: payment={payment['id']}, balanced settlement, idempotent replay")
