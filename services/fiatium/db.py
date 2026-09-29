import hashlib
from functools import lru_cache

from sqlalchemy import create_engine, text

from fiatium.config import settings


@lru_cache
def engine():
    return create_engine(settings().database_url, pool_pre_ping=True, pool_size=10, max_overflow=50)


def lock(conn, resource: str):
    """Transaction-owned database lock; unique constraints remain the final guard."""
    resource_hash = hashlib.sha256(resource.encode()).hexdigest()
    conn.execute(
        text("""
        DECLARE @r int;
        EXEC @r = sys.sp_getapplock @Resource=:resource, @LockMode='Exclusive',
            @LockOwner='Transaction', @LockTimeout=15000;
        IF @r < 0 THROW 51001, 'Resource busy; retry with the same idempotency key', 1;
        """),
        {"resource": resource_hash},
    )


def one(conn, query, **params):
    row = conn.execute(text(query), params).mappings().first()
    return dict(row) if row else None


def many(conn, query, **params):
    return [dict(row) for row in conn.execute(text(query), params).mappings()]
