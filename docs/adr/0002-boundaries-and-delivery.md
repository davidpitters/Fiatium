# ADR 0002 — One application, independent API and worker processes

Accepted for the first slice, 2026-09-29.

Start with one Python package with explicit domain, ledger, payment, reconciliation,
worker and API boundaries. It contains SQL adapters, not a shared mutable ORM model.
SQLAlchemy Core manages transactions, Alembic manages migrations. Extract services
only when ownership or scale demands it. The API, publisher and consumer run as
separate processes from one image. React uses the actual authenticated API.

Redpanda supplies Kafka-compatible transport. Event v1 contains ID, tenant,
payment ID/partition key, type, timestamp, correlation and causation IDs. The first
event has null causation. The publisher marks sent only after broker acknowledgement.
It holds a SQL row lock during its bounded broker call; this is a deliberate low-volume
tradeoff. A crash after publish can republish the record. Producer idempotence does
not extend across SQL and broker commits: delivery is at least once.

The worker disables automatic offset storage/commit. It commits an offset only after
SQL effects or a parking record commit. SQL failure prevents advancing beyond that
record. Same-payment database locks and unique operation keys permit concurrent
consumers and redelivery. A terminal payment tolerates a reordered request event.
Only payment.requested v1 exists; other event types/versions are parked, not guessed.
Transient timeout/delay scenarios settle on the second attempt; persistent ledger
failure parks after three attempts. Raw poison payloads are not stored, only hashes.
Parking is durable and inspectable, but approved replay is a later milestone.

For a host without Docker/Kafka, `local-once` explicitly drains the outbox through
the same handler. It proves SQL business behavior, not broker offset handling,
partition behavior, rebalance or process-kill recovery. Do not run that adapter
against the same database as the real publisher/consumer.

Demo bearer tokens map to server-side tenant and viewer/operator roles. Clients
cannot select tenants using request payloads or headers. Tokens stay in browser
memory. This is a local demo security model; OAuth/OIDC, rate limiting, rotation,
row-level database security and public deployment hardening are future work.

Container tags and dependencies are pinned. OS package repositories remain mutable;
image digests, supply-chain scanning and rebuild policy remain follow-up work.
Docker/Kubernetes execution, distributed tracing, dashboards, cloud and AI approval
are not claimed as complete. There are no placeholder AI or Kubernetes services.
