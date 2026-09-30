import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fiatium.api import app
from fiatium.config import settings
from fiatium.db import engine, many, one
from fiatium.domain import InvoiceInput, PaymentInput, Problem, RefundInput
from fiatium.payments import create_invoice, submit_payment
from fiatium.processing import process
from fiatium.reconciliation import reconcile
from fiatium.refunds import refund_payment
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_SQL_TESTS") != "1", reason="SQL Server integration is opt-in"
    ),
]


@pytest.fixture
def settled():
    tenant, customer_id = "test-" + uuid4().hex, str(uuid4())
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,'Refund test')"),
            {"id": customer_id, "t": tenant},
        )
    invoice = create_invoice(tenant, InvoiceInput(customer_id=customer_id, amount=4500))
    payment, _ = submit_payment(
        tenant, PaymentInput(invoice_id=invoice["id"], amount=4500), str(uuid4()), str(uuid4())
    )
    with engine().connect() as conn:
        event = json.loads(
            one(conn, "SELECT payload FROM outbox_events WHERE payment_id=:p", p=payment["id"])[
                "payload"
            ]
        )
    process(event)
    return tenant, invoice["id"], payment["id"], event


def request(payment_id, reason="Customer requested simulated return"):
    return RefundInput(payment_id=payment_id, reason=reason)


def test_fifty_refund_requests_reverse_once_and_preserve_original(settled):
    tenant, invoice_id, payment_id, _ = settled
    with engine().connect() as conn:
        original = many(
            conn,
            """SELECT l.id,l.account,l.amount FROM journal_lines l
            JOIN journal_transactions j ON j.id=l.journal_id
            WHERE j.tenant=:t AND j.operation=:op""",
            t=tenant,
            op=f"settlement:{payment_id}",
        )
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(
            pool.map(
                lambda _: refund_payment(tenant, request(payment_id), "same-refund", str(uuid4())),
                range(50),
            )
        )
    assert sum(not replay for _, replay in results) == 1
    assert all(result == results[0][0] for result, _ in results)
    result = results[0][0]
    with engine().connect() as conn:
        preserved = many(
            conn,
            "SELECT id,account,amount FROM journal_lines WHERE journal_id=:j",
            j=result["original_journal_id"],
        )
        assert sorted(original, key=lambda line: line["id"]) == sorted(
            preserved, key=lambda line: line["id"]
        )
        assert many(
            conn,
            "SELECT account,amount FROM journal_lines WHERE journal_id=:j ORDER BY account",
            j=result["journal_id"],
        ) == [{"account": "AR", "amount": 4500}, {"account": "CASH", "amount": -4500}]
        assert one(conn, "SELECT status,active FROM payments WHERE id=:p", p=payment_id) == {
            "status": "refunded",
            "active": False,
        }
        assert (
            one(conn, "SELECT status FROM invoices WHERE id=:i", i=invoice_id)["status"] == "open"
        )
    assert reconcile(tenant)["findings"] == []
    with pytest.raises(Problem) as conflict:
        refund_payment(tenant, request(payment_id, "Changed reason"), "same-refund", str(uuid4()))
    assert conflict.value.status == 409


def test_competing_keys_cannot_refund_twice(settled):
    tenant, _, payment_id, _ = settled

    def submit(_):
        try:
            refund_payment(tenant, request(payment_id), str(uuid4()), str(uuid4()))
            return True
        except Problem as exc:
            assert exc.status == 409
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(submit, range(10))) == 1


def test_late_worker_event_does_not_resettle_refund_or_new_payment(settled):
    tenant, invoice_id, payment_id, event = settled
    refund_payment(tenant, request(payment_id), str(uuid4()), str(uuid4()))
    replacement, _ = submit_payment(
        tenant, PaymentInput(invoice_id=invoice_id, amount=4500), str(uuid4()), str(uuid4())
    )
    process({**event, "id": str(uuid4())})
    with engine().connect() as conn:
        assert (
            one(conn, "SELECT status FROM payments WHERE id=:p", p=payment_id)["status"]
            == "refunded"
        )
        assert (
            one(conn, "SELECT status FROM payments WHERE id=:p", p=replacement["id"])["status"]
            == "initiated"
        )
        replacement_event = json.loads(
            one(conn, "SELECT payload FROM outbox_events WHERE payment_id=:p", p=replacement["id"])[
                "payload"
            ]
        )
    process(replacement_event)
    assert reconcile(tenant)["findings"] == []


def test_refund_rollback_does_not_leave_reversal_or_change_state(settled, monkeypatch):
    import fiatium.refunds as module

    tenant, _, payment_id, _ = settled
    real_post = module.post

    def fail_after_post(*args):
        real_post(*args)
        raise RuntimeError("Injected failure before refund record")

    with monkeypatch.context() as patch:
        patch.setattr(module, "post", fail_after_post)
        with pytest.raises(RuntimeError):
            refund_payment(tenant, request(payment_id), "retryable", str(uuid4()))
    with engine().connect() as conn:
        assert not one(conn, "SELECT id FROM refunds WHERE tenant=:t", t=tenant)
        assert not one(
            conn,
            "SELECT id FROM journal_transactions WHERE tenant=:t AND operation LIKE 'refund:%'",
            t=tenant,
        )
        assert (
            one(conn, "SELECT status FROM payments WHERE id=:p", p=payment_id)["status"]
            == "settled"
        )
    refund_payment(tenant, request(payment_id), "retryable", str(uuid4()))
    assert reconcile(tenant)["findings"] == []


@pytest.mark.parametrize("fault", ["not_settled", "processor_amount"])
def test_refund_rejects_unsettled_or_inconsistent_evidence(settled, fault):
    tenant, _, payment_id, _ = settled
    with engine().begin() as conn:
        statement = (
            "UPDATE payments SET status='processing' WHERE id=:p"
            if fault == "not_settled"
            else "UPDATE processor_attempts SET amount=4501 WHERE payment_id=:p"
        )
        conn.execute(text(statement), {"p": payment_id})
    with pytest.raises(Problem) as rejected:
        refund_payment(tenant, request(payment_id), str(uuid4()), str(uuid4()))
    assert rejected.value.status == 409
    with engine().connect() as conn:
        assert not one(conn, "SELECT id FROM refunds WHERE tenant=:t", t=tenant)


def test_refund_evidence_cannot_be_changed_or_deleted(settled):
    tenant, _, payment_id, _ = settled
    result, _ = refund_payment(tenant, request(payment_id), str(uuid4()), str(uuid4()))
    for sql in (
        "UPDATE refunds SET reason='changed' WHERE id=:id",
        "DELETE FROM refunds WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            with engine().begin() as conn:
                conn.execute(text(sql), {"id": result["id"]})
    assert reconcile(tenant)["findings"] == []


def test_reconciliation_flags_refunded_state_without_evidence(settled):
    tenant, _, payment_id, _ = settled
    with engine().begin() as conn:
        conn.execute(text("UPDATE payments SET status='refunded' WHERE id=:p"), {"p": payment_id})
    assert {f["kind"] for f in reconcile(tenant)["findings"]} == {"refund_status_mismatch"}


def test_refund_role_tenant_and_payload_checks(settled, monkeypatch):
    tenant, _, payment_id, _ = settled
    monkeypatch.setattr(
        settings(),
        "tokens",
        {
            "owner": {"tenant": tenant, "role": "operator"},
            "viewer": {"tenant": tenant, "role": "viewer"},
            "other": {"tenant": "other-" + tenant, "role": "operator"},
        },
    )
    with TestClient(app) as client:
        payload = {"payment_id": payment_id, "reason": "Simulated return"}
        for token, expected in [("viewer", 403), ("other", 404), ("missing", 401)]:
            response = client.post(
                "/api/refunds",
                json=payload,
                headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "refund-key"},
            )
            assert response.status_code == expected
        headers = {"Authorization": "Bearer owner", "Idempotency-Key": "refund-key"}
        assert (
            client.post(
                "/api/refunds", json={**payload, "amount": 100}, headers=headers
            ).status_code
            == 422
        )
        assert (
            client.post(
                "/api/refunds", json={**payload, "reason": "  "}, headers=headers
            ).status_code
            == 422
        )
        response = client.post("/api/refunds", json=payload, headers=headers)
        assert response.status_code == 201
        replay = client.post("/api/refunds", json=payload, headers=headers)
        assert replay.json() == response.json()
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert (
            len(client.get(f"/api/payments/{payment_id}", headers=headers).json()["refunds"]) == 1
        )
