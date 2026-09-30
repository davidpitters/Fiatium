"""Full simulated refunds preserve original settlements."""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    # Revision 0001 used an automatically named CHECK; discover only that column's check.
    op.execute("""
        DECLARE @name sysname;
        SELECT @name=name FROM sys.check_constraints
        WHERE parent_object_id=OBJECT_ID('payments')
          AND parent_column_id=COLUMNPROPERTY(OBJECT_ID('payments'),'status','ColumnId');
        IF @name IS NULL THROW 51020, 'Payment status constraint not found', 1;
        EXEC('ALTER TABLE payments DROP CONSTRAINT ' + @name);
        ALTER TABLE payments ADD CONSTRAINT ck_payment_status CHECK(status IN
            ('initiated','processing','authorized','settled','failed','refunded'));
        CREATE TABLE refunds (
            id varchar(36) NOT NULL PRIMARY KEY,
            tenant varchar(64) NOT NULL,
            payment_id varchar(36) NOT NULL,
            amount bigint NOT NULL CHECK(amount > 0 AND amount <= 1000000000000),
            currency char(3) NOT NULL CHECK(currency='CAD'),
            reason nvarchar(240) NOT NULL,
            processor_reference varchar(64) NOT NULL UNIQUE,
            original_journal_id varchar(36) NOT NULL REFERENCES journal_transactions(id),
            journal_id varchar(36) NOT NULL UNIQUE REFERENCES journal_transactions(id),
            correlation_id varchar(36) NOT NULL,
            created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
            CONSTRAINT uq_refund_payment UNIQUE(tenant,payment_id),
            FOREIGN KEY(tenant,payment_id) REFERENCES payments(tenant,id)
        );
    """)
    op.execute("""
        CREATE TRIGGER refund_guard ON refunds AFTER UPDATE,DELETE AS
        BEGIN
            THROW 51021, 'Refund evidence is immutable', 1;
        END;
    """)


def downgrade():
    raise RuntimeError("Refund history must be preserved; use a forward migration")
