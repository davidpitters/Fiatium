import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import text

from fiatium.db import engine, lock, one
from fiatium.domain import InvoiceInput, PaymentInput, Problem, fingerprint
from fiatium.ledger import post


def create_invoice(tenant: str, data: InvoiceInput):
    invoice_id = str(uuid4())
    with engine().begin() as conn:
        if not one(
            conn,
            "SELECT id FROM customers WHERE tenant=:t AND id=:id",
            t=tenant,
            id=str(data.customer_id),
        ):
            raise Problem(404, "Customer not found")
        conn.execute(
            text("""INSERT INTO invoices(id,tenant,customer_id,amount,currency)
                VALUES(:id,:t,:customer,:amount,'CAD')"""),
            {
                "id": invoice_id,
                "t": tenant,
                "customer": str(data.customer_id),
                "amount": data.amount,
            },
        )
        post(conn, tenant, f"invoice:{invoice_id}", "AR", "REVENUE", data.amount)
    return {"id": invoice_id, "status": "open"}


def enqueue(conn, tenant, payment_id, correlation_id, causation_id=None):
    event_id = str(uuid4())
    event = {
        "id": event_id,
        "version": 1,
        "type": "payment.requested",
        "tenant": tenant,
        "payment_id": payment_id,
        "partition_key": payment_id,
        "correlation_id": correlation_id,
        "causation_id": causation_id,
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    conn.execute(
        text("""INSERT INTO outbox_events(id,tenant,payment_id,event_type,payload)
            VALUES(:id,:t,:p,'payment.requested',:payload)"""),
        {"id": event_id, "t": tenant, "p": payment_id, "payload": json.dumps(event)},
    )


def submit_payment(tenant: str, data: PaymentInput, key: str, correlation_id: str):
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    request_hash = fingerprint(data)
    with engine().begin() as conn:
        lock(conn, f"idempotency:{tenant}:payments:{key_hash}")
        existing = one(
            conn,
            """SELECT fingerprint,response FROM idempotency
            WHERE tenant=:t AND operation='payments' AND key_hash=:k""",
            t=tenant,
            k=key_hash,
        )
        if existing:
            if existing["fingerprint"] != request_hash:
                raise Problem(409, "Idempotency key was already used with a different request")
            return json.loads(existing["response"]), True
        lock(conn, f"invoice:{tenant}:{data.invoice_id}")
        invoice = one(
            conn,
            "SELECT * FROM invoices WHERE tenant=:t AND id=:id",
            t=tenant,
            id=str(data.invoice_id),
        )
        if not invoice:
            raise Problem(404, "Invoice not found")
        if invoice["amount"] != data.amount:
            raise Problem(409, "This slice supports only full invoice payments")
        if invoice["status"] != "open" or one(
            conn,
            "SELECT id FROM payments WHERE tenant=:t AND invoice_id=:id AND active=1",
            t=tenant,
            id=str(data.invoice_id),
        ):
            raise Problem(409, "Invoice already has an active or settled payment")
        payment_id = str(uuid4())
        conn.execute(
            text("""INSERT INTO payments(id,tenant,invoice_id,amount,currency,status,scenario)
                VALUES(:id,:t,:invoice,:amount,'CAD','initiated',:scenario)"""),
            {
                "id": payment_id,
                "t": tenant,
                "invoice": str(data.invoice_id),
                "amount": data.amount,
                "scenario": data.scenario.value,
            },
        )
        response = {
            "id": payment_id,
            "status": "initiated",
            "amount": data.amount,
            "currency": "CAD",
        }
        conn.execute(
            text("""INSERT INTO idempotency(tenant,operation,key_hash,fingerprint,response)
                VALUES(:t,'payments',:k,:f,:r)"""),
            {"t": tenant, "k": key_hash, "f": request_hash, "r": json.dumps(response)},
        )
        enqueue(conn, tenant, payment_id, correlation_id)
        return response, False
