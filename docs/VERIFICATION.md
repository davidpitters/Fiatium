# Verification record — 2026-09-29

## Observed on this workstation

Windows, local SQL Server **16.0.1200.5**, Python **3.12.14**, Node **24.19.0**,
pnpm **11.25.0**. SQL Windows authentication required execution outside the sandbox.
The sandbox launcher later failed during setup refresh; final commands ran with
approved execution. No database or application credentials were printed or committed.

The original workspace contained only the two project briefs. A dedicated
`FiatiumDev` database was created and seeded. Existing databases were not modified.
Tests create uniquely named tenants and retain test ledger history.

| Command / check | Observed result |
| --- | --- |
| `scripts/init-local.ps1` | Created local configuration, migrated real SQL Server, seeded customer/invoice |
| `python -m pytest tests/test_domain.py -q` | 8 passed initially |
| `FIATIUM_SQL_TESTS=1 python -m pytest -q -p no:cacheprovider` | **21 passed**, 2.51 seconds in the final SQL run |
| `python -m ruff check services tests db/migrations scripts` | Passed |
| `python -m ruff format --check services tests db/migrations scripts` | Passed, 19 files |
| `pnpm build` in apps/web | TypeScript and Vite production build passed |
| `python scripts/smoke.py --direct` | Real HTTP customer → invoice → payment → replay → settlement → balanced journal → reconciliation passed |
| `node scripts/browser-smoke.cjs` | Headless installed Chrome: login, customer, invoice, payment and evidence inspection passed; mobile page-width assertion passed |
| Desktop/mobile screenshot review | Actual browser screenshots inspected; preview saved in docs/images |

SQL tests cover strict money inputs, 50 simultaneous same-key submissions, conflicting
payloads, concurrent different-key double-payment prevention, concurrent duplicate
callbacks, posting failure after processor success, recovery, decline, timeout,
delayed authorization, duplicate scenario, poison/persistent-failure parking,
publisher crash rollback, immutable/balanced SQL journals, tenant isolation and role
checks, missing/extra journals, amount/status mismatches.

The publisher crash test injects a send callback failure, not a real broker crash.
The worker tests call the real SQL adapter directly; they do not prove Kafka offset
or consumer-rebalance behavior. The HTTP smoke uses the explicit direct development
transport. Browser tests submit asynchronous work; they do not claim worker settlement.

One dependency warning remains: Starlette deprecates its httpx TestClient adapter
in favor of httpx2. Tests passed using the explicitly pinned httpx version.
Initial lint/type errors and a browser-test row-selection race were fixed. The first
browser attempt could not launch the bundled Chromium, so installed Chrome was used.
Port 8080 belongs to an existing Windows listener; the local preview uses 8088.

## Not executed / acceptance gates still open

- Docker/Compose image build and startup, real Redpanda transport, restricted Compose
  SQL principal, CI execution, Linux dependency installation and image scans.
- Process-kill recovery, consumer rebalance, sustained load or performance measurements.
- Kubernetes/Helm/HPA/KEDA, probes for workers, dashboards and distributed telemetry.
- AI agent, approval/replay, refund/reversal and cloud deployment.

Do not treat the local test duration as a throughput benchmark. No cloud resources,
external deployment, pushes, paid services or real payment systems were used.

## Reproducing on this machine

The virtual environment is `.venv`. Node is available at:
`C:\Users\DavidPitters\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe`.
The bundled pnpm launcher is at:
`C:\Users\DavidPitters\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd`.
Put the Node directory on PATH when using pnpm scripts. Normal development installs
of the versions listed above work with the README commands.

The browser smoke uses the locked `apps/web` Playwright dependency and installed
Chrome. From the project root run `node scripts/browser-smoke.cjs` after starting the
API/web and running the HTTP smoke once. It reads the ignored local token without
printing it. Set `BROWSER_CHANNEL=msedge` to use installed Edge.

## Version-selection references

- [FastAPI release notes](https://fastapi.tiangolo.com/release-notes/)
- [Vite setup requirements](https://vite.dev/guide/)
- [SQL Server published container tags](https://mcr.microsoft.com/v2/mssql/server/tags/list)
- [SQL Server container requirements](https://learn.microsoft.com/en-us/sql/linux/quickstart-install-connect-docker?pivots=cs1-bash&tabs=cli)
- [Redpanda versions and upgrades](https://docs.redpanda.com/streaming/current/upgrade/rolling-upgrade/)
- [Official Python image tags](https://hub.docker.com/_/python/tags?name=3.12&page=1)

Dependency versions were resolved from the package registries and locked in the
repository. Container references were selected from published versions; pulls and
builds remain unverified. SQL Server 2022 was chosen to match the existing local server.
