import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fiatium.db import engine, many, one
from fiatium.domain import InvoiceInput, PaymentInput
from fiatium.payments import create_invoice, submit_payment
from fiatium.processing import process
from fiatium.reconciliation import reconcile
from fiatium.worker import publish_one
from sqlalchemy import text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_SQL_TESTS") != "1", reason="SQL Server integration is opt-in"
    ),
]


@pytest.mark.parametrize("boundary", ["processor-gap", "effect-gap", "outbox-gap"])
def test_recovery_after_real_process_kill(boundary, tmp_path):
    tenant, customer_id = "test-" + uuid4().hex, str(uuid4())
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,'Process crash test')"),
            {"id": customer_id, "t": tenant},
        )
    invoice = create_invoice(tenant, InvoiceInput(customer_id=customer_id, amount=6700))
    payment, _ = submit_payment(
        tenant, PaymentInput(invoice_id=invoice["id"], amount=6700), str(uuid4()), str(uuid4())
    )
    with engine().connect() as conn:
        event = json.loads(
            one(conn, "SELECT payload FROM outbox_events WHERE payment_id=:p", p=payment["id"])[
                "payload"
            ]
        )
    event_file, checkpoint = tmp_path / "event.json", tmp_path / "ready"
    event_file.write_text(json.dumps(event), encoding="utf-8")
    helper = Path(__file__).parent / "helpers" / "crash_worker.py"
    with (tmp_path / "child.log").open("w", encoding="utf-8") as output:
        child = subprocess.Popen(
            [sys.executable, str(helper), boundary, str(event_file), str(checkpoint)],
            stdout=output,
            stderr=output,
        )
        try:
            deadline = time.monotonic() + 20
            while not checkpoint.exists():
                assert child.poll() is None, (
                    f"Child failed before checkpoint; see {tmp_path}/child.log"
                )
                assert time.monotonic() < deadline, "Child checkpoint timed out"
                time.sleep(0.05)
            child.kill()
            child.wait(timeout=10)
            assert child.returncode != 0
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
    if boundary == "processor-gap":
        assert {f["kind"] for f in reconcile(tenant)["findings"]} == {
            "missing_journal",
            "status_mismatch",
        }
    elif boundary == "outbox-gap":
        with engine().connect() as conn:
            assert (
                one(conn, "SELECT sent_at FROM outbox_events WHERE id=:id", id=event["id"])[
                    "sent_at"
                ]
                is None
            )
        sent = []
        assert publish_one(lambda key, payload: sent.append(json.loads(payload)), event["id"])
        assert sent == [event]
    process(event)
    process(event)
    with engine().connect() as conn:
        assert (
            one(conn, "SELECT COUNT(*) AS n FROM processor_attempts WHERE tenant=:t", t=tenant)["n"]
            == 1
        )
        journals = many(
            conn,
            "SELECT id FROM journal_transactions WHERE tenant=:t AND operation=:op",
            t=tenant,
            op=f"settlement:{payment['id']}",
        )
        assert len(journals) == 1
        assert one(
            conn,
            "SELECT COUNT(*) AS n,SUM(amount) AS balance FROM journal_lines WHERE journal_id=:id",
            id=journals[0]["id"],
        ) == {"n": 2, "balance": 0}
    assert reconcile(tenant)["findings"] == []
