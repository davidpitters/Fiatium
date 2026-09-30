import os
import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fiatium.db import engine, one
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("FIATIUM_SQL_TESTS") != "1", reason="SQL Server integration is opt-in"
    ),
]


def test_line_insertion_cannot_race_a_draft_posting():
    journal_id, tenant = str(uuid4()), "test-" + uuid4().hex
    with engine().begin() as conn:
        permitted = conn.execute(
            text("SELECT HAS_PERMS_BY_NAME('journal_transactions','OBJECT','INSERT')")
        ).scalar_one()
        if not permitted:
            pytest.skip("Direct draft fault injection needs migration/admin database permissions")
        conn.execute(
            text("""INSERT INTO journal_transactions(id,tenant,operation,currency)
            VALUES(:id,:t,'draft-race','CAD')"""),
            {"id": journal_id, "t": tenant},
        )
        conn.execute(
            text("""INSERT INTO journal_lines(journal_id,account,amount)
            VALUES(:id,'AR',100),(:id,'REVENUE',-100)"""),
            {"id": journal_id},
        )
    posted, release = threading.Event(), threading.Event()

    def post_and_hold():
        with engine().begin() as conn:
            conn.execute(
                text("UPDATE journal_transactions SET status='posted' WHERE id=:id"),
                {"id": journal_id},
            )
            posted.set()
            assert release.wait(10), "Test coordinator failed to release posting transaction"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(post_and_hold)
        try:
            assert posted.wait(5)
            with engine().connect() as conn:
                try:
                    conn.execute(text("SET LOCK_TIMEOUT 300"))
                    with pytest.raises(DBAPIError) as blocked:
                        conn.execute(
                            text(
                                "INSERT INTO journal_lines(journal_id,account,amount) "
                                "VALUES(:id,'CASH',1)"
                            ),
                            {"id": journal_id},
                        )
                    assert "1222" in str(blocked.value.orig)
                finally:
                    conn.rollback()
                    conn.execute(text("SET LOCK_TIMEOUT -1"))
                    conn.commit()
        finally:
            release.set()
        future.result(timeout=5)
    with pytest.raises(DBAPIError) as immutable:
        with engine().begin() as conn:
            conn.execute(
                text("INSERT INTO journal_lines(journal_id,account,amount) VALUES(:id,'CASH',1)"),
                {"id": journal_id},
            )
    assert "51013" in str(immutable.value.orig)
    with engine().connect() as conn:
        assert one(
            conn,
            "SELECT SUM(amount) AS balance,COUNT(*) AS n FROM journal_lines WHERE journal_id=:id",
            id=journal_id,
        ) == {"balance": 0, "n": 2}
