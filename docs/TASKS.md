# Implementation checklist

- [x] Read brief and inspect tools; workspace initially contained only the briefs.
- [x] Foundation implementation: dependency locks, SQL migration, API/web/worker, Compose, CI.
- [x] Initial ledger slice: invoice + payment + journal, 50-request SQL concurrency tests.
- [x] Async implementation and SQL fault tests: outbox/inbox, retries, parking, reconciliation.
- [x] Local HTTP smoke and desktop/mobile browser journey; actual screenshots.
- [x] Full simulated refund/reversal operation and concurrent draft-posting fault tests.
- [ ] Verify real broker acknowledgement, rebalance and process-kill recovery.
- [ ] Validate Compose startup on a Docker host.
- [ ] Local Kubernetes/Helm, probes, scaling, measured failure drill.
- [ ] Bounded AI investigation, tenant-scoped tools, human replay approval.
- [ ] Portfolio demo, measured load, actual screenshots.

Initial environment: Windows; SQL Server service available. Python/Node available
through bundled runtimes. Docker, Helm, kubectl and kind not on PATH.

See VERIFICATION.md for executed commands. Foundation's full fresh-Compose gate and
the distributed recovery gates remain open; checked implementation tasks do not
claim those milestones have passed all acceptance requirements.
