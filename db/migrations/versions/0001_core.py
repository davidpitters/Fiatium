"""Core schema and database-enforced ledger safeguards."""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    source = Path(__file__).parents[2] / "schema.sql"
    for batch in source.read_text(encoding="utf-8").split("\nGO\n"):
        if batch.strip():
            op.get_bind().exec_driver_sql(batch)


def downgrade():
    raise RuntimeError("Ledger history must be preserved; use a forward migration")
