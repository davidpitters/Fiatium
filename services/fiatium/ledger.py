"""Narrow posting boundary. No service imports a shared mutable ORM model."""

import json
from uuid import uuid4

from sqlalchemy import text


def post(conn, tenant: str, operation: str, debit: str, credit: str, amount: int):
    journal_id = str(uuid4())
    conn.execute(
        text("EXEC post_journal :id, :tenant, :operation, :lines"),
        {
            "id": journal_id,
            "tenant": tenant,
            "operation": operation,
            "lines": json.dumps(
                [{"account": debit, "amount": amount}, {"account": credit, "amount": -amount}]
            ),
        },
    )
    return journal_id
