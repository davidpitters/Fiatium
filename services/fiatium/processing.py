"""Payment-worker SQL adapter. Processor facts commit before ledger settlement."""

from uuid import uuid4

from sqlalchemy import text

from fiatium.db import engine, lock, one
from fiatium.domain import processor_outcome
from fiatium.ledger import post


class RetryLater(Exception):
    pass


def process(event: dict, *, fail_before_post=False):
    tenant, payment_id, event_id = event["tenant"], event["payment_id"], event["id"]
    # Separate durable fake-processor fact, emulating an external system's response.
    with engine().begin() as conn:
        lock(conn, f"payment:{tenant}:{payment_id}")
        if one(
            conn,
            "SELECT event_id FROM inbox_receipts WHERE consumer='payment-worker' AND event_id=:id",
            id=event_id,
        ):
            return
        payment = one(
            conn, "SELECT * FROM payments WHERE tenant=:t AND id=:id", t=tenant, id=payment_id
        )
        if not payment:
            raise ValueError("Event refers to an unknown tenant/payment")
        if payment["status"] in ("settled", "failed"):
            receipt(conn, event_id)
            return
        previous = one(
            conn,
            "SELECT TOP (1) * FROM processor_attempts "
            "WHERE tenant=:t AND payment_id=:p ORDER BY attempt DESC",
            t=tenant,
            p=payment_id,
        )
        if previous and previous["outcome"] in ("settled", "declined"):
            outcome = previous["outcome"]
        else:
            attempt = previous["attempt"] + 1 if previous else 1
            outcome = processor_outcome(payment["scenario"], attempt)
            conn.execute(
                text("""INSERT INTO processor_attempts
                (id,tenant,payment_id,attempt,outcome,amount) VALUES(:id,:t,:p,:n,:o,:a)"""),
                {
                    "id": str(uuid4()),
                    "t": tenant,
                    "p": payment_id,
                    "n": attempt,
                    "o": outcome,
                    "a": payment["amount"],
                },
            )
        status = "authorized" if outcome == "authorized" else "processing"
        conn.execute(
            text("UPDATE payments SET status=:s WHERE tenant=:t AND id=:p"),
            {"s": status, "t": tenant, "p": payment_id},
        )
    if outcome in ("timeout", "authorized"):
        raise RetryLater(outcome)
    if fail_before_post or payment["scenario"] == "ledger_failure":
        raise RetryLater("Injected failure after durable processor success")
    with engine().begin() as conn:
        lock(conn, f"payment:{tenant}:{payment_id}")
        payment = one(
            conn, "SELECT * FROM payments WHERE tenant=:t AND id=:id", t=tenant, id=payment_id
        )
        if payment["status"] not in ("settled", "failed"):
            if outcome == "settled":
                post(conn, tenant, f"settlement:{payment_id}", "CASH", "AR", payment["amount"])
                conn.execute(
                    text("UPDATE invoices SET status='paid' WHERE tenant=:t AND id=:id"),
                    {"t": tenant, "id": payment["invoice_id"]},
                )
            conn.execute(
                text("UPDATE payments SET status=:s,active=:a WHERE tenant=:t AND id=:p"),
                {
                    "s": "settled" if outcome == "settled" else "failed",
                    "a": 1 if outcome == "settled" else 0,
                    "t": tenant,
                    "p": payment_id,
                },
            )
        receipt(conn, event_id)


def receipt(conn, event_id):
    conn.execute(
        text("""IF NOT EXISTS(SELECT 1 FROM inbox_receipts
        WHERE consumer='payment-worker' AND event_id=:id)
        INSERT INTO inbox_receipts(consumer,event_id) VALUES('payment-worker',:id)"""),
        {"id": event_id},
    )


def park(event_id, tenant, payment_id, reason):
    with engine().begin() as conn:
        lock(conn, f"park:{event_id}")
        conn.execute(
            text("""IF NOT EXISTS(SELECT 1 FROM parked_events WHERE id=:id)
            INSERT INTO parked_events(id,tenant,payment_id,reason) VALUES(:id,:t,:p,:r)"""),
            {"id": event_id, "t": tenant, "p": payment_id, "r": reason[:240]},
        )
