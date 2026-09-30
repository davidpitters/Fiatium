"""Full refunds against the in-process fake processor, atomically recorded in SQL.

This intentionally does not model a remote refund call or its failure window.
"""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import text

from fiatium.db import engine, lock, many, one
from fiatium.domain import Problem, RefundInput, fingerprint
from fiatium.ledger import post


def refund_payment(tenant: str, data: RefundInput, key: str, correlation_id: str):
    key_hash = hashlib.sha256(key.encode()).hexdigest()
    request_hash = fingerprint(data)
    payment_id = str(data.payment_id)
    with engine().begin() as conn:
        lock(conn, f"idempotency:{tenant}:refunds:{key_hash}")
        previous = one(
            conn,
            """SELECT fingerprint,response FROM idempotency
            WHERE tenant=:t AND operation='refunds' AND key_hash=:k""",
            t=tenant,
            k=key_hash,
        )
        if previous:
            if previous["fingerprint"] != request_hash:
                raise Problem(409, "Idempotency key was already used with a different refund")
            return json.loads(previous["response"]), True
        # Read the immutable invoice reference first; then lock in invoice -> payment order.
        payment = one(
            conn, "SELECT * FROM payments WHERE tenant=:t AND id=:p", t=tenant, p=payment_id
        )
        if not payment:
            raise Problem(404, "Payment not found")
        lock(conn, f"invoice:{tenant}:{payment['invoice_id']}")
        lock(conn, f"payment:{tenant}:{payment_id}")
        payment = one(
            conn, "SELECT * FROM payments WHERE tenant=:t AND id=:p", t=tenant, p=payment_id
        )
        if payment["status"] != "settled":
            raise Problem(409, "Only a settled payment can be refunded, once, in full")
        original = one(
            conn,
            """SELECT id FROM journal_transactions WHERE tenant=:t
            AND operation=:op AND status='posted'""",
            t=tenant,
            op=f"settlement:{payment_id}",
        )
        attempt = one(
            conn,
            """SELECT TOP (1) outcome,amount FROM processor_attempts
            WHERE tenant=:t AND payment_id=:p ORDER BY attempt DESC""",
            t=tenant,
            p=payment_id,
        )
        if (
            not original
            or not attempt
            or attempt != {"outcome": "settled", "amount": payment["amount"]}
        ):
            raise Problem(409, "Settlement evidence is inconsistent; reconcile before refunding")
        lines = many(
            conn, "SELECT account,amount FROM journal_lines WHERE journal_id=:j", j=original["id"]
        )
        expected = {("CASH", payment["amount"]), ("AR", -payment["amount"])}
        if len(lines) != 2 or {(line["account"], line["amount"]) for line in lines} != expected:
            raise Problem(409, "Settlement journal is inconsistent; reconcile before refunding")
        refund_id = str(uuid4())
        journal_id = post(conn, tenant, f"refund:{refund_id}", "AR", "CASH", payment["amount"])
        # A deterministic synthetic processor receipt; no external effect occurs here.
        processor_reference = f"fake-refund:{refund_id}"
        conn.execute(
            text("""INSERT INTO refunds
            (id,tenant,payment_id,amount,currency,reason,processor_reference,
             original_journal_id,journal_id,correlation_id)
            VALUES(:id,:t,:p,:amount,'CAD',:reason,:reference,:original,:journal,:correlation)"""),
            {
                "id": refund_id,
                "t": tenant,
                "p": payment_id,
                "amount": payment["amount"],
                "reason": data.reason,
                "reference": processor_reference,
                "original": original["id"],
                "journal": journal_id,
                "correlation": correlation_id,
            },
        )
        conn.execute(
            text("UPDATE payments SET status='refunded',active=0 WHERE tenant=:t AND id=:p"),
            {"t": tenant, "p": payment_id},
        )
        conn.execute(
            text("UPDATE invoices SET status='open' WHERE tenant=:t AND id=:i"),
            {"t": tenant, "i": payment["invoice_id"]},
        )
        result = {
            "id": refund_id,
            "payment_id": payment_id,
            "status": "refunded",
            "amount": payment["amount"],
            "currency": "CAD",
            "journal_id": journal_id,
            "original_journal_id": original["id"],
            "processor_reference": processor_reference,
        }
        conn.execute(
            text("""INSERT INTO idempotency(tenant,operation,key_hash,fingerprint,response)
            VALUES(:t,'refunds',:k,:f,:r)"""),
            {"t": tenant, "k": key_hash, "f": request_hash, "r": json.dumps(result)},
        )
        return result, False
