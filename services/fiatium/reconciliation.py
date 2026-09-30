from uuid import uuid4

from sqlalchemy import text

from fiatium.db import engine, many


def reconcile(tenant: str):
    run_id = str(uuid4())
    findings = []
    with engine().execution_options(isolation_level="SERIALIZABLE").begin() as conn:
        # A short serializable read gives this bounded demo run a consistent snapshot.
        end = conn.execute(
            text("""INSERT INTO reconciliation_runs(id,tenant,window_start,window_end)
            OUTPUT inserted.window_end
            VALUES(:id,:t,'2000-01-01',SYSUTCDATETIME())"""),
            {"id": run_id, "t": tenant},
        ).scalar_one()
        rows = many(
            conn,
            """SELECT p.id,p.amount,p.status,a.id AS processor_id,
            a.outcome,a.amount AS processor_amount,j.id AS journal_id,
            (SELECT SUM(l.amount) FROM journal_lines l
             WHERE l.journal_id=j.id AND l.account='CASH') AS journal_amount
            FROM payments p OUTER APPLY (SELECT TOP (1) * FROM processor_attempts a
                WHERE a.tenant=p.tenant AND a.payment_id=p.id ORDER BY attempt DESC) a
            LEFT JOIN journal_transactions j ON j.tenant=p.tenant
                AND j.operation=CONCAT('settlement:',p.id) AND j.status='posted'
            WHERE p.tenant=:t AND p.created_at >= '2000-01-01' AND p.created_at <=
                (SELECT window_end FROM reconciliation_runs WHERE id=:run_id)""",
            t=tenant,
            run_id=run_id,
        )
        for row in rows:
            kinds = []
            if row["outcome"] == "settled" and not row["journal_id"]:
                kinds.append(
                    ("missing_journal", "Processor settled; settlement journal is missing")
                )
            if row["journal_id"] and row["outcome"] != "settled":
                kinds.append(
                    ("extra_journal", "Settlement journal exists without processor settlement")
                )
            if (row["outcome"] == "settled") != (row["status"] in ("settled", "refunded")):
                kinds.append(("status_mismatch", "Processor and internal settlement status differ"))
            if any(
                value is not None and value != row["amount"]
                for value in (row["processor_amount"], row["journal_amount"])
            ):
                kinds.append(("amount_mismatch", "Processor/payment/journal amounts differ"))
            refunds = many(
                conn,
                """SELECT r.amount,r.original_journal_id,r.journal_id,j.status,
                j.operation,(SELECT SUM(l.amount) FROM journal_lines l
                WHERE l.journal_id=r.journal_id AND l.account='CASH') AS cash
                FROM refunds r LEFT JOIN journal_transactions j ON j.id=r.journal_id
                WHERE r.tenant=:t AND r.payment_id=:p""",
                t=tenant,
                p=row["id"],
            )
            if (row["status"] == "refunded") != bool(refunds):
                kinds.append(("refund_status_mismatch", "Refund evidence and payment state differ"))
            for refund in refunds:
                if refund["status"] != "posted" or refund["cash"] is None:
                    kinds.append(("missing_refund_journal", "Refund lacks a posted cash reversal"))
                elif (
                    refund["amount"] != row["amount"]
                    or refund["cash"] != -row["amount"]
                    or refund["original_journal_id"] != row["journal_id"]
                ):
                    kinds.append(
                        ("refund_amount_mismatch", "Refund does not reverse its settlement")
                    )
            for kind, detail in kinds:
                finding = {
                    "id": str(uuid4()),
                    "run_id": run_id,
                    "tenant": tenant,
                    "payment_id": row["id"],
                    "kind": kind,
                    "detail": detail,
                }
                conn.execute(
                    text("""INSERT INTO reconciliation_findings
                    (id,run_id,tenant,payment_id,kind,detail)
                    VALUES(:id,:run_id,:tenant,:payment_id,:kind,:detail)"""),
                    finding,
                )
                findings.append(finding)
    return {
        "id": run_id,
        "window_start": "2000-01-01T00:00:00Z",
        "window_end": end.isoformat() + "Z",
        "findings": findings,
    }
