import os
from uuid import uuid4

import pytest
from fiatium.db import engine
from fiatium.domain import InvoiceInput, PaymentInput
from fiatium.ledger import post
from fiatium.payments import create_invoice, submit_payment
from fiatium.reconciliation import reconcile
from sqlalchemy import text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_SQL_TESTS") != "1", reason="SQL Server integration is opt-in"
    ),
]


def test_reconciliation_detects_extra_posting_and_amount_mismatch():
    tenant = "test-" + uuid4().hex
    customer_id = str(uuid4())
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,'Recon test')"),
            {"id": customer_id, "t": tenant},
        )
    invoice = create_invoice(tenant, InvoiceInput(customer_id=customer_id, amount=1000))
    payment, _ = submit_payment(
        tenant, PaymentInput(invoice_id=invoice["id"], amount=1000), str(uuid4()), str(uuid4())
    )
    # Deliberately inject a balanced but unjustified settlement; reconciliation is
    # about business facts, not only arithmetic balance.
    with engine().begin() as conn:
        post(conn, tenant, f"settlement:{payment['id']}", "CASH", "AR", 999)
    findings = reconcile(tenant)["findings"]
    assert {f["kind"] for f in findings} == {"extra_journal", "amount_mismatch"}
