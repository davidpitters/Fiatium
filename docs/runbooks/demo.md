# First-slice demo

1. Start the local app following README. Sign in using the generated local token.
2. Select the seeded invoice, or create a simulated customer and invoice (integer cents).
3. Choose `success`, submit, and run `python -m fiatium.worker local-once` in the
   broker-free path. Compose instead processes automatically.
4. The invoice becomes paid; payment becomes settled. Inspect processor/event IDs.
   Journal shows AR/revenue on issue, cash/AR on settlement.
5. Create another invoice and choose `ledger_failure`. After processing, inspect
   the settled processor attempt, processing payment and parked event.
6. Run reconciliation. It records `missing_journal` and `status_mismatch` findings.
   It does not change the ledger. Repeated runs keep historical findings.
7. Try `decline` (no cash posting), `timeout` or `delayed` (second attempt settles),
   and `duplicate` (same callback handled twice, one posting).

The `ledger_failure` flag intentionally remains a persistent fault. No approved
replay endpoint exists yet. Do not manually edit posted journals to fix the demo.
The fault-injection integration test separately demonstrates recovery after a
transient posting failure using the same saved processor result.

## Next operational gate

On a Docker host, run Compose and the HTTP smoke without `--direct`, then kill a
worker during processing and check a single settlement journal per payment.
Record commands and observed outcomes. Broker redelivery/rebalance behavior has not
yet been measured here. Kubernetes pod termination, HPA and KEDA drills are pending.
