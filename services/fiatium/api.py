import secrets
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from fiatium.config import settings
from fiatium.db import engine, many, one
from fiatium.domain import CustomerInput, InvoiceInput, PaymentInput, Problem
from fiatium.payments import create_invoice, submit_payment
from fiatium.reconciliation import reconcile

app = FastAPI(title="Fiatium — simulated payments", version="0.1.0")


@app.middleware("http")
async def correlation(request: Request, call_next):
    request.state.correlation_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = request.state.correlation_id
    return response


@app.exception_handler(Problem)
async def problem_handler(request: Request, exc: Problem):
    return JSONResponse(
        status_code=exc.status,
        content={
            "type": "about:blank",
            "status": exc.status,
            "detail": exc.detail,
            "correlation_id": request.state.correlation_id,
        },
        media_type="application/problem+json",
    )


@app.exception_handler(DBAPIError)
async def database_error(request: Request, exc: DBAPIError):
    # Never return SQL text, connection strings or driver messages to clients.
    return await problem_handler(request, Problem(503, "Database operation failed; retry safely"))


def principal(authorization: Annotated[str | None, Header()] = None):
    token = (authorization or "").removeprefix("Bearer ")
    for key, identity in settings().tokens.items():
        if secrets.compare_digest(token.encode(), key.encode()):
            if identity.get("tenant") and identity.get("role") in ("viewer", "operator"):
                return identity
    raise Problem(401, "A configured demo bearer token is required")


Identity = Annotated[dict, Depends(principal)]


def operator(identity):
    if identity["role"] != "operator":
        raise Problem(403, "Operator role required")


@app.get("/health/live")
def live():
    return {"status": "ok", "simulation": True}


@app.get("/health/ready")
def ready():
    with engine().connect() as conn:
        conn.execute(text("SELECT TOP (1) id FROM customers"))
    if not settings().tokens:
        raise Problem(503, "Demo authentication is not configured")
    return {"status": "ready"}


@app.get("/api/me")
def me(identity: Identity):
    return identity


@app.get("/api/customers")
def customers(identity: Identity):
    with engine().connect() as conn:
        return many(
            conn,
            "SELECT TOP (200) * FROM customers WHERE tenant=:t ORDER BY created_at DESC",
            t=identity["tenant"],
        )


@app.post("/api/customers", status_code=201)
def customer(data: CustomerInput, identity: Identity):
    operator(identity)
    customer_id = str(uuid4())
    with engine().begin() as conn:
        conn.execute(
            text("INSERT INTO customers(id,tenant,name) VALUES(:id,:t,:name)"),
            {"id": customer_id, "t": identity["tenant"], "name": data.name},
        )
    return {"id": customer_id, "name": data.name}


@app.get("/api/invoices")
def invoices(identity: Identity):
    with engine().connect() as conn:
        return many(
            conn,
            "SELECT TOP (200) * FROM invoices WHERE tenant=:t ORDER BY created_at DESC",
            t=identity["tenant"],
        )


@app.post("/api/invoices", status_code=201)
def invoice(data: InvoiceInput, identity: Identity):
    operator(identity)
    return create_invoice(identity["tenant"], data)


@app.post("/api/payments", status_code=202)
def payment(
    data: PaymentInput,
    identity: Identity,
    request: Request,
    idempotency_key: Annotated[str, Header(min_length=1, max_length=128)],
):
    operator(identity)
    result, replay = submit_payment(
        identity["tenant"], data, idempotency_key, request.state.correlation_id
    )
    return JSONResponse(
        result, status_code=202, headers={"Idempotency-Replayed": str(replay).lower()}
    )


@app.get("/api/payments")
def payments(identity: Identity):
    with engine().connect() as conn:
        return many(
            conn,
            "SELECT TOP (200) * FROM payments WHERE tenant=:t ORDER BY created_at DESC",
            t=identity["tenant"],
        )


@app.get("/api/payments/{payment_id}")
def payment_detail(payment_id: UUID, identity: Identity):
    tenant, pid = identity["tenant"], str(payment_id)
    with engine().connect() as conn:
        result = one(conn, "SELECT * FROM payments WHERE tenant=:t AND id=:p", t=tenant, p=pid)
        if not result:
            raise Problem(404, "Payment not found")
        result["attempts"] = many(
            conn,
            "SELECT * FROM processor_attempts WHERE tenant=:t AND payment_id=:p ORDER BY attempt",
            t=tenant,
            p=pid,
        )
        result["events"] = many(
            conn,
            "SELECT id,event_type,created_at,sent_at FROM outbox_events "
            "WHERE tenant=:t AND payment_id=:p ORDER BY created_at",
            t=tenant,
            p=pid,
        )
        result["parked"] = many(
            conn,
            "SELECT id,reason FROM parked_events WHERE tenant=:t AND payment_id=:p",
            t=tenant,
            p=pid,
        )
        return result


@app.get("/api/journals")
def journals(identity: Identity):
    with engine().connect() as conn:
        return many(
            conn,
            """SELECT TOP (400) j.id,j.operation,j.currency,j.created_at,
            l.account,l.amount FROM journal_transactions j JOIN journal_lines l ON l.journal_id=j.id
            WHERE j.tenant=:t AND j.status='posted' ORDER BY j.created_at DESC,l.id""",
            t=identity["tenant"],
        )


@app.post("/api/reconciliation-runs", status_code=201)
def reconciliation(identity: Identity):
    operator(identity)
    return reconcile(identity["tenant"])


@app.get("/api/reconciliation-findings")
def findings(identity: Identity):
    with engine().connect() as conn:
        return many(
            conn,
            """SELECT TOP (200) f.* FROM reconciliation_findings f
            JOIN reconciliation_runs r ON f.run_id=r.id
            WHERE f.tenant=:t ORDER BY r.created_at DESC""",
            t=identity["tenant"],
        )
