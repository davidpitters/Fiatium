# Worker recovery acceptance

## Locally verified SQL/process boundaries

With the database migrated and Windows integrated SQL access available:

```powershell
$env:FIATIUM_SQL_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_process_recovery_sql.py tests/test_worker_delivery.py -q -p no:cacheprovider
```

The SQL drill creates unique test tenants and child Python processes. The parent
waits for an explicit checkpoint, kills only its child process, then retries the
same event. Children have deadlines and are cleaned up on failure.

| Checkpoint | Evidence after termination | Recovery |
| --- | --- | --- |
| Processor success committed, before posting | Missing journal and status mismatch | Replay saved success; one settlement |
| Settlement and inbox committed | Balanced posting retained | Redelivery has no second effect |
| Publisher inside outbox transaction | sent_at remains NULL; SQL lock released | Record is eligible to publish again |

These are actual process terminations against real SQL Server. The local publisher
case uses a callback in place of the broker. These tests do not prove Kafka
delivery, partition reassignment or container behavior.

## Real-broker drill (provided; not yet executed here)

Run against the disposable Fiatium development stack. It needs permission to
create/delete uniquely named broker topics. It retains SQL test history and never
deletes the main payment topic.

```powershell
docker compose --env-file .env.compose up --build --wait --wait-timeout 240
docker compose --env-file .env.compose --profile test build verify
docker compose --env-file .env.compose stop publisher worker
docker compose --env-file .env.compose run --rm -e FIATIUM_BROKER_TESTS=1 verify python -m pytest tests/test_broker_recovery.py -q -p no:cacheprovider
docker compose --env-file .env.compose start publisher worker
```

Stop the normal publisher so it cannot claim the drill's outbox row and route it
onto the normal topic. Restart publisher/worker after a failed drill too, once logs
have been inspected. The API and broker stay running.

Each test creates a new topic and consumer group:

1. Obtain a real broker delivery acknowledgement, pause before marking the SQL
   outbox sent, then kill the publisher and publish again. Consume two identical
   messages; assert one processor fact and settlement, and committed offset 2.
2. Consume and commit SQL settlement, pause before acknowledging Kafka, then kill
   the consumer. Confirm no committed group offset. A new member must receive the
   event and commit offset 1 without duplicating the posting.

Checkpoints, polling and child termination have bounded timeouts. Topics are deleted
in finally blocks; broker outage can prevent cleanup. Their prefix is `fiatium.drill.`
followed by a random ID. Do not delete other topics. Child logs are in pytest's
per-test temporary directory. CI runs this drill after the ordinary SQL suite;
configuration is not evidence of a successful CI run.

## Application behavior

Publisher success requires one successful delivery callback and no outstanding
message after flush. Timeout, queue overflow and Kafka errors leave the outbox
pending and retry after a delay.

Consumers disable automatic commit and automatic offset storage. SQL/handler
failure, poll error or a failed/partial synchronous commit closes the assignment and
rejoins after two seconds from committed offsets. The failed session never polls
later messages. This avoids indefinitely holding a stale assignment through a SQL
outage. Unexpected programming errors still terminate the process for its supervisor.

SQL can commit before an acknowledgement is lost. Replay must remain idempotent;
there is no exactly-once delivery claim. With automatic commits disabled, close
does not commit unfinished work. See the
[Confluent Python client API](https://docs.confluent.io/platform/current/clients/confluent-kafka-python/html/index.html)
for commit results and close behavior.
