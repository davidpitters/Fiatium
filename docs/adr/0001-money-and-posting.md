# ADR 0001 — Integer money and transactional posting

Accepted for the first slice, 2026-09-29.

SQL Server is the required source of truth. Amounts are BIGINT CAD cents with a
demo limit of 1,000,000,000,000 cents. Strict Pydantic integers reject floats,
strings, booleans, zero, negatives and unsupported currencies. The cap also keeps
individual API amounts within JavaScript's exact integer range.

An invoice posts debit AR / credit REVENUE. Settlement posts debit CASH / credit
AR. Authorization and timeouts never post cash. Declines release the invoice's
active payment slot. Settlement keeps that slot occupied, preventing a second payment.
Partial payments and refunds are not exposed in this slice.

`post_journal` builds a draft header and signed lines, then transitions the header
to posted in a SQL transaction nested within the caller's transaction. A trigger
checks at least two lines and a zero DECIMAL(38,0) sum before posting. Currency is
fixed to CAD on the header. Positive lines debit, negative lines credit. Foreign
keys validate account codes. Triggers reject all line insert/update/delete and
header update/delete operations involving a posted journal. Lock hints serialize
the balance check with line changes. The application reads only posted journals.

Compose's application SQL principal cannot directly write ledger tables or the
chart of accounts; ownership chaining allows only the posting procedure to do so.
Administrators can disable safeguards: this is application immutability, not a
cryptographic defense against a database administrator. Local Windows verification
used the owner's administrative login; restricted Compose principal remains unverified.

Idempotency is tenant + operation + SHA-256(key). Keys are case-sensitive before
hashing. A canonical fingerprint includes amount, currency, invoice and scenario.
The original 202 body is stored and returned on replay even after settlement;
GET payment returns current status. A database transaction-owned application lock
serializes same-key contenders; the SQL primary key is the final uniqueness guard.
The invoice has a separate transaction lock and filtered unique active-payment index.
Contenders wait up to 15 seconds; timeout/deadlock returns 503 and clients retry
with the same key. Different payloads return 409. Validation failures are not cached.

Outbox insertion and payment/idempotency creation are one transaction. Processor
facts deliberately commit separately before payment/journal settlement, making
missing-ledger discrepancies visible and recoverable. Repeated settlement is
guarded by a payment lock, terminal state, inbox receipt and unique journal operation.

Corrections must eventually use reversing/replacement transactions. No journal
editing or refund endpoint is implemented. Do not infer full accounting coverage.
