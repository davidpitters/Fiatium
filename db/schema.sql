CREATE TABLE customers (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    name nvarchar(120) NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_customer_tenant UNIQUE(tenant,id)
);
CREATE TABLE invoices (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    customer_id varchar(36) NOT NULL,
    amount bigint NOT NULL CHECK(amount > 0 AND amount <= 1000000000000),
    currency char(3) NOT NULL CHECK(currency='CAD'),
    status varchar(16) NOT NULL DEFAULT 'open' CHECK(status IN ('open','paid')),
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_invoice_tenant UNIQUE(tenant,id),
    FOREIGN KEY(tenant,customer_id) REFERENCES customers(tenant,id)
);
CREATE TABLE payments (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    invoice_id varchar(36) NOT NULL,
    amount bigint NOT NULL CHECK(amount > 0 AND amount <= 1000000000000),
    currency char(3) NOT NULL CHECK(currency='CAD'),
    status varchar(16) NOT NULL CHECK(status IN
        ('initiated','processing','authorized','settled','failed')),
    active bit NOT NULL DEFAULT 1,
    scenario varchar(24) NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_payment_tenant UNIQUE(tenant,id),
    FOREIGN KEY(tenant,invoice_id) REFERENCES invoices(tenant,id)
);
CREATE UNIQUE INDEX uq_active_invoice ON payments(tenant,invoice_id) WHERE active=1;
CREATE TABLE idempotency (
    tenant varchar(64) NOT NULL,
    operation varchar(32) NOT NULL,
    key_hash char(64) NOT NULL,
    fingerprint char(64) NOT NULL,
    response nvarchar(max) NOT NULL,
    PRIMARY KEY(tenant,operation,key_hash)
);
CREATE TABLE accounts (
    code varchar(16) NOT NULL PRIMARY KEY,
    name varchar(80) NOT NULL
);
INSERT INTO accounts VALUES ('AR','Accounts receivable'),('REVENUE','Simulated revenue'),
    ('CASH','Simulated cash / clearing');
CREATE TABLE journal_transactions (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    operation varchar(80) NOT NULL,
    currency char(3) NOT NULL CHECK(currency='CAD'),
    status varchar(10) NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','posted')),
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_journal_operation UNIQUE(tenant,operation)
);
CREATE TABLE journal_lines (
    id bigint IDENTITY PRIMARY KEY,
    journal_id varchar(36) NOT NULL REFERENCES journal_transactions(id),
    account varchar(16) NOT NULL REFERENCES accounts(code),
    amount bigint NOT NULL CHECK(amount <> 0 AND amount BETWEEN -1000000000000 AND 1000000000000)
);
CREATE INDEX ix_lines_journal ON journal_lines(journal_id);
GO
CREATE TRIGGER journal_guard ON journal_transactions AFTER INSERT,UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS(SELECT 1 FROM deleted WHERE status='posted')
        THROW 51010, 'Posted journals are immutable', 1;
    IF EXISTS(SELECT 1 FROM inserted i LEFT JOIN deleted d ON d.id=i.id
        WHERE d.id IS NULL AND i.status='posted')
        THROW 51011, 'Journal must be built before posting', 1;
    IF EXISTS(SELECT 1 FROM inserted i OUTER APPLY (
        SELECT COUNT_BIG(*) AS n, SUM(CAST(amount AS decimal(38,0))) AS balance
        FROM journal_lines WITH (UPDLOCK,HOLDLOCK) WHERE journal_id=i.id
    ) l WHERE i.status='posted' AND (l.n < 2 OR l.balance <> 0))
        THROW 51012, 'Journal must have at least two balanced lines', 1;
END;
GO
CREATE TRIGGER lines_guard ON journal_lines AFTER INSERT,UPDATE,DELETE AS
BEGIN
    SET NOCOUNT ON;
    IF EXISTS(SELECT 1 FROM journal_transactions j WITH (UPDLOCK,HOLDLOCK)
        WHERE j.status='posted' AND j.id IN (
            SELECT journal_id FROM inserted UNION SELECT journal_id FROM deleted
        )) THROW 51013, 'Posted journal lines are immutable', 1;
END;
GO
CREATE PROCEDURE post_journal
    @id varchar(36), @tenant varchar(64), @operation varchar(80), @lines nvarchar(max)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;
    BEGIN TRANSACTION;
    INSERT INTO journal_transactions(id,tenant,operation,currency)
        VALUES(@id,@tenant,@operation,'CAD');
    INSERT INTO journal_lines(journal_id,account,amount)
        SELECT @id,account,amount FROM OPENJSON(@lines)
        WITH(account varchar(16),amount bigint);
    UPDATE journal_transactions SET status='posted' WHERE id=@id;
    COMMIT TRANSACTION;
END;
GO
CREATE TABLE outbox_events (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    payment_id varchar(36) NOT NULL,
    event_type varchar(40) NOT NULL,
    payload nvarchar(max) NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    sent_at datetime2 NULL,
    FOREIGN KEY(tenant,payment_id) REFERENCES payments(tenant,id)
);
CREATE INDEX ix_outbox_unsent ON outbox_events(sent_at,created_at);
CREATE TABLE inbox_receipts (
    consumer varchar(32) NOT NULL,
    event_id varchar(36) NOT NULL,
    processed_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    PRIMARY KEY(consumer,event_id)
);
CREATE TABLE processor_attempts (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    payment_id varchar(36) NOT NULL,
    attempt int NOT NULL,
    outcome varchar(16) NOT NULL CHECK(outcome IN ('settled','authorized','declined','timeout')),
    amount bigint NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME(),
    CONSTRAINT uq_processor_attempt UNIQUE(tenant,payment_id,attempt),
    FOREIGN KEY(tenant,payment_id) REFERENCES payments(tenant,id)
);
CREATE TABLE parked_events (
    id varchar(64) NOT NULL PRIMARY KEY,
    tenant varchar(64) NULL,
    payment_id varchar(36) NULL,
    reason varchar(240) NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE TABLE reconciliation_runs (
    id varchar(36) NOT NULL PRIMARY KEY,
    tenant varchar(64) NOT NULL,
    window_start datetime2 NOT NULL,
    window_end datetime2 NOT NULL,
    created_at datetime2 NOT NULL DEFAULT SYSUTCDATETIME()
);
CREATE TABLE reconciliation_findings (
    id varchar(36) NOT NULL PRIMARY KEY,
    run_id varchar(36) NOT NULL REFERENCES reconciliation_runs(id),
    tenant varchar(64) NOT NULL,
    payment_id varchar(36) NOT NULL,
    kind varchar(32) NOT NULL,
    detail nvarchar(500) NOT NULL
);
