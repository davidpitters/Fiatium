import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from fiatium.api import app
from fiatium.config import settings
from fiatium.db import engine, many, one
from fiatium.domain import InvoiceInput, PaymentInput, Problem
from fiatium.payments import create_invoice, submit_payment
from fiatium.processing import RetryLater, process
from fiatium.reconciliation import reconcile
from fiatium.worker import handle, publish_one
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_SQL_TESTS") != "1",
        reason="Set FIATIUM_SQL_TESTS=1 against a migrated SQL Server",
    ),
]


@pytest.fixture
def tenant():
    # Unique tenants preserve immutable history and avoid destructive test cleanup.
    return "test-" + uuid4().hex


def invoice(tenant, amount=12500):
    customer_id = str(uuid4())
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,'Test customer')"),
            {"id": customer_id, "t": tenant},
        )
    return create_invoice(tenant, InvoiceInput(customer_id=customer_id, amount=amount))["id"]


def payment(tenant, scenario="success"):
    data = PaymentInput(invoice_id=invoice(tenant), amount=12500, scenario=scenario)
    result, _ = submit_payment(tenant, data, str(uuid4()), str(uuid4()))
    with engine().connect() as conn:
        event = one(conn, "SELECT payload FROM outbox_events WHERE payment_id=:id", id=result["id"])
    return result, json.loads(event["payload"])


def test_fifty_concurrent_identical_requests(tenant):
    data = PaymentInput(invoice_id=invoice(tenant), amount=12500)
    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(
            pool.map(lambda _: submit_payment(tenant, data, "same-key", str(uuid4())), range(50))
        )
    assert len({r[0]["id"] for r in results}) == 1
    assert sum(not r[1] for r in results) == 1
    assert all(r[0] == results[0][0] for r in results)
    with pytest.raises(Problem) as error:
        submit_payment(tenant, data.model_copy(update={"amount": 12501}), "same-key", str(uuid4()))
    assert error.value.status == 409
    with engine().connect() as conn:
        assert one(conn, "SELECT COUNT(*) AS n FROM payments WHERE tenant=:t", t=tenant)["n"] == 1
        assert (
            one(conn, "SELECT COUNT(*) AS n FROM outbox_events WHERE tenant=:t", t=tenant)["n"] == 1
        )


def test_different_keys_cannot_double_pay_one_invoice(tenant):
    data = PaymentInput(invoice_id=invoice(tenant), amount=12500)

    def submit(_):
        try:
            return submit_payment(tenant, data, str(uuid4()), str(uuid4()))[0]["id"]
        except Problem as exc:
            assert exc.status == 409
            return None

    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(r is not None for r in pool.map(submit, range(10))) == 1


def test_concurrent_duplicate_callbacks_have_one_posting(tenant):
    result, event = payment(tenant)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: process(event), range(10)))
    # Same business operation with a different delivery ID is also harmless.
    process({**event, "id": str(uuid4())})
    with engine().connect() as conn:
        assert (
            one(conn, "SELECT status FROM payments WHERE id=:id", id=result["id"])["status"]
            == "settled"
        )
        journals = many(
            conn,
            "SELECT id FROM journal_transactions WHERE tenant=:t AND operation=:op",
            t=tenant,
            op=f"settlement:{result['id']}",
        )
        assert len(journals) == 1
        assert one(
            conn,
            "SELECT SUM(amount) AS balance,COUNT(*) AS n FROM journal_lines WHERE journal_id=:id",
            id=journals[0]["id"],
        ) == {"balance": 0, "n": 2}
        assert (
            one(conn, "SELECT COUNT(*) AS n FROM processor_attempts WHERE tenant=:t", t=tenant)["n"]
            == 1
        )


def test_success_survives_posting_failure_and_recovery(tenant):
    result, event = payment(tenant)
    with pytest.raises(RetryLater):
        process(event, fail_before_post=True)
    findings = reconcile(tenant)["findings"]
    assert {f["kind"] for f in findings} == {"missing_journal", "status_mismatch"}
    assert all(f["payment_id"] == result["id"] for f in findings)
    process(event)
    assert reconcile(tenant)["findings"] == []


@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("decline", "failed"),
        ("timeout", "settled"),
        ("delayed", "settled"),
        ("duplicate", "settled"),
    ],
)
def test_scenarios(tenant, scenario, expected):
    result, event = payment(tenant, scenario)
    handle(json.dumps(event).encode())
    with engine().connect() as conn:
        assert (
            one(conn, "SELECT status FROM payments WHERE id=:id", id=result["id"])["status"]
            == expected
        )
    assert reconcile(tenant)["findings"] == []


def test_poison_and_persistent_failure_are_parked(tenant):
    _, event = payment(tenant, "ledger_failure")
    handle(json.dumps(event).encode())
    handle(b'{"version":99,"secret":"must not be stored"}')
    with engine().connect() as conn:
        assert one(conn, "SELECT id FROM parked_events WHERE tenant=:t", t=tenant)
        assert not one(
            conn, "SELECT event_id FROM inbox_receipts WHERE event_id=:id", id=event["id"]
        )


def test_publish_crash_preserves_outbox_and_repeated_effect_is_unique(tenant):
    _, event = payment(tenant)
    delivered = []

    def crash(key, payload):
        delivered.append(payload)
        raise RuntimeError("Crash after broker accepted payload")

    # Target only this test's event; never mark another tenant's work as published.
    with pytest.raises(RuntimeError):
        publish_one(crash, event["id"])
    selected = json.loads(delivered[0])
    with engine().connect() as conn:
        assert (
            one(conn, "SELECT sent_at FROM outbox_events WHERE id=:id", id=selected["id"])[
                "sent_at"
            ]
            is None
        )
    publish_one(lambda key, payload: delivered.append(payload), event["id"])
    assert delivered[0] == delivered[1]
    # Own tenant's duplicate delivery proves the downstream operation separately.
    process(event)
    process(event)
    assert reconcile(tenant)["findings"] == []


def test_database_rejects_unbalanced_and_mutated_journals(tenant):
    invoice_id = invoice(tenant)
    with pytest.raises(DBAPIError):
        with engine().begin() as conn:
            conn.execute(
                text("EXEC post_journal :id,:t,:op,:lines"),
                {
                    "id": str(uuid4()),
                    "t": tenant,
                    "op": "invalid",
                    "lines": '[{"account":"AR","amount":100},{"account":"REVENUE","amount":-99}]',
                },
            )
    with engine().connect() as conn:
        assert not one(
            conn,
            "SELECT id FROM journal_transactions WHERE tenant=:t AND operation='invalid'",
            t=tenant,
        )
        journal = one(
            conn,
            "SELECT id FROM journal_transactions WHERE tenant=:t AND operation=:op",
            t=tenant,
            op=f"invoice:{invoice_id}",
        )["id"]
    for sql in [
        "UPDATE journal_lines SET amount=1 WHERE journal_id=:id",
        "DELETE FROM journal_lines WHERE journal_id=:id",
        "INSERT INTO journal_lines(journal_id,account,amount) VALUES(:id,'AR',1)",
        "UPDATE journal_transactions SET status='draft' WHERE id=:id",
        "DELETE FROM journal_transactions WHERE id=:id",
    ]:
        with pytest.raises(DBAPIError):
            with engine().begin() as conn:
                conn.execute(text(sql), {"id": journal})


def test_api_authorization_and_tenant_isolation(tenant, monkeypatch):
    result, _ = payment(tenant)
    monkeypatch.setattr(
        settings(),
        "tokens",
        {
            "a": {"tenant": tenant, "role": "operator"},
            "b": {"tenant": "other-" + tenant, "role": "operator"},
            "v": {"tenant": tenant, "role": "viewer"},
        },
    )
    with TestClient(app) as client:
        assert client.get("/api/payments").status_code == 401
        assert (
            client.get(
                f"/api/payments/{result['id']}", headers={"Authorization": "Bearer b"}
            ).status_code
            == 404
        )
        for path in ("payments", "customers", "invoices", "journals", "reconciliation-findings"):
            assert client.get(f"/api/{path}", headers={"Authorization": "Bearer b"}).json() == []
        assert (
            client.post(
                "/api/customers", json={"name": "Denied"}, headers={"Authorization": "Bearer v"}
            ).status_code
            == 403
        )
        response = client.get(
            f"/api/payments/{result['id']}", headers={"Authorization": "Bearer a"}
        )
        assert response.status_code == 200
        assert response.headers["X-Correlation-ID"]
