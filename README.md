# Ledge

Ledge is an event-driven financial data pipeline that turns unreliable bank
transaction updates into durable, auditable ledger history.

Provider data is not a clean stream: notifications are repeated, pending charges
become posted under new identifiers, amounts change, transactions disappear, and
workers fail halfway through processing. Ledge is designed around those failure
modes instead of assuming every event arrives once and in order.

The project currently provides a tested local vertical slice using PostgreSQL,
FastAPI, SQLAlchemy, Alembic, and both deterministic and real Plaid Sandbox
adapters. It can create a Sandbox Item, map supported bank accounts, ingest real
cursor updates, and preserve each change as balanced ledger history. AWS event
delivery is the next infrastructure milestone.

## Product goal

Ledge will give one sandbox consumer a trustworthy view of checking, savings, and
credit activity while preserving enough history to explain every correction.
Later read models will use that foundation for recurring-charge detection,
30-day cash-flow projection, and a transparent safe-to-spend estimate.

Ledge never moves money. It is a read-only reconciliation and reporting system.

## How it works

```mermaid
flowchart LR
    webhook[Transaction notification] --> api[FastAPI intake]
    api --> inbox[(PostgreSQL event inbox)]
    inbox --> worker[Leased event processor]
    worker --> provider[Plaid transaction adapter]
    provider --> sync[Cursor-based synchronization]
    sync --> ledger[(PostgreSQL ledger)]
    ledger --> reads[User-scoped read API]

    plaid[Plaid Sandbox] --> provider
    sqs[SQS and Lambda - planned] -. invokes .-> worker
```

1. FastAPI accepts a normalized transaction notification and persists it before
   returning `202 Accepted`.
2. Duplicate provider event IDs return the existing inbox record. Reusing an ID
   with different data is rejected.
3. A worker claims the event in a short database transaction, then releases the
   row lock before performing provider I/O.
4. The provider adapter returns added, modified, removed, and pending-to-posted
   transaction changes from a durable cursor.
5. Ledge writes the entire fetched update and new cursor in one PostgreSQL
   transaction.
6. Corrections append balanced reversals and replacements instead of rewriting
   sealed journal history.
7. The worker finalizes only if it still owns the claim, preventing an expired
   worker from overwriting a newer attempt.

## Current capabilities

- Double-entry journals using integer cents and zero-sum postings
- Immutable sealed journal history enforced by both Python and PostgreSQL
- Added, modified, removed, and pending-to-posted transaction reconciliation
- Duplicate-safe provider transaction and webhook handling
- Multi-page cursor synchronization with atomic ledger and cursor commits
- Real Plaid Sandbox Item bootstrap, account mapping, and `/transactions/sync`
  translation
- Continuous Sandbox exercises with optional Plaid-generated transaction refreshes
- Cursor race detection and complete rollback after injected failures
- Durable event attempts, five-minute processing leases, and abandoned-work
  recovery
- Per-attempt claim tokens that fence stale workers
- Categorized failure metadata without persisting sensitive exception messages
- Processing timestamps and duration results for latency and retry measurements
- User-scoped account, transaction, and synchronization-status reads
- PostgreSQL-backed readiness and explicit unavailable responses

## Reliability model

Ledge treats duplicate delivery and retries as normal behavior.

```text
pending
  -> processing (attempt count incremented, unique token assigned)
  -> processed

processing
  -> failed (safe failure category retained)
  -> processing again on retry

expired processing lease
  -> reclaimed with a new token
  -> old worker is rejected if it later tries to finalize
```

Provider calls do not run inside the inbox claim transaction. This avoids holding
a PostgreSQL connection and row lock during network latency while retaining
durable ownership across process crashes.

Each complete synchronization batch uses a separate atomic transaction. If any
transaction change fails, neither partial ledger history nor the new provider
cursor becomes visible.

## API

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Service and PostgreSQL readiness |
| `GET` | `/accounts` | Configured user's financial accounts |
| `GET` | `/transactions` | Filtered and paginated current projection |
| `GET` | `/sync-status` | Provider connection and committed cursor state |
| `POST` | `/webhooks/transactions` | Durable normalized notification intake |

The local MVP uses one configured `LEDGE_USER_ID`. Every resource query is already
scoped by that identity, but verified request authentication is not implemented.

## Verified checkpoint

The current repository has:

- 199 passing tests on Python 3.14
- Real PostgreSQL integration tests rather than SQLite substitutes
- Nine reversible Alembic migrations with automated schema-drift detection
- Concurrency coverage for active leases, expired claim recovery, cursor races,
  and stale-worker fencing
- Failure-injection coverage proving transaction, journal, posting, and cursor
  changes roll back together

One local run against Plaid's official dynamic Sandbox profile imported 125 real
Sandbox transactions into 125 journals and 250 balanced postings in 1.175 seconds.
Three later refresh cycles advanced the cursor while processing `+15/-3`,
`+12/-6`, and `+12/-6` provider changes in 0.590, 0.788, and 0.511 seconds. A
repeat poll against an unchanged Item made no ledger changes in 0.395 seconds.
These are reproducible local Sandbox observations, not production benchmarks or
throughput claims.

## Technology

- Python 3.12+
- FastAPI and Pydantic
- PostgreSQL 16
- SQLAlchemy 2
- Alembic
- psycopg 3
- pytest, pytest-cov, and Ruff
- Docker Compose for disposable local PostgreSQL instances

The accounting domain has no dependency on FastAPI, SQLAlchemy, Plaid, or an AWS
SDK. Infrastructure adapters call inward through application-owned interfaces.

## Run locally

Create a virtual environment and install the project:

```bash
python -m venv venv
venv/bin/pip install -e '.[dev]'
```

Start PostgreSQL and configure the local environment:

```bash
docker compose up -d postgres
export LEDGE_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge'
export LEDGE_TEST_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge_test'
export LEDGE_USER_ID='11111111-1111-1111-1111-111111111111'
venv/bin/alembic upgrade head
```

Run the complete test suite and coverage report:

```bash
venv/bin/pytest
venv/bin/pytest --cov=src --cov-report=term-missing
```

Run the API:

```bash
venv/bin/uvicorn api.app:create_app --factory --reload
```

Interactive OpenAPI documentation is available at
`http://127.0.0.1:8000/docs`. A newly migrated development database contains no
accounts or provider connections until test or sandbox data is added.

### Exercise the real Plaid Sandbox pipeline

Copy `.env.example` to the ignored `.env` file and add Sandbox credentials there.
Never commit `.env` or a generated access-token file. Load the configuration,
create a refreshable Sandbox Item, and perform an initial synchronization:

```bash
set -a
source .env
set +a
venv/bin/ledge-plaid-sandbox --dynamic-transactions
venv/bin/ledge-plaid-sync
```

Exercise cursor advancement and pending-to-posted changes over several real Plaid
Sandbox refreshes:

```bash
venv/bin/ledge-plaid-sync --iterations 4 --interval 5 --refresh-between
```

The CLI prints each provider page count, added/modified/removed count, cursor
outcome, and elapsed time. See [`docs/development.md`](docs/development.md) for
isolated profiles, token-file handling, and troubleshooting.

## Current boundaries

The following pieces are intentionally not implemented yet:

- Plaid Link, production institution credentials, webhook verification, and
  automatic webhook-to-worker dispatch
- Transaction versions and stale-update ordering
- Automatic SQS handoff and Lambda invocation
- S3 raw-event archive, dead-letter queue, controlled replay, and CloudWatch
  telemetry
- Authentication and multi-user request identity
- Calculated balance, recurring-charge, projection, and dashboard views

## Roadmap

1. **AWS event delivery:** Connect intake to SQS, run the proven processor from
   Lambda, archive events in S3, and add a dead-letter queue.
2. **Operational evidence:** Emit CloudWatch metrics and run documented failure
   and load experiments for latency, retries, throughput, and recovery.
3. **Plaid webhook completion:** Validate real Plaid notifications and enqueue the
   existing durable processing workflow.
4. **Consumer read models:** Add balances, recurring charges, cash-flow projection,
   and safe-to-spend calculations.
5. **Thin dashboard:** Make the pipeline's results and health understandable on
   desktop and mobile.

## Documentation

- [`docs/README.md`](docs/README.md) is the detailed documentation index and
  recommended reading order.
- [`docs/domain-model.md`](docs/domain-model.md) explains transactions, postings,
  journals, reversals, and the role of the in-memory `LedgerState`.
- [`docs/architecture.md`](docs/architecture.md) explains system boundaries,
  transaction ownership, and the target AWS design.
- [`docs/invariants.md`](docs/invariants.md) records financial and processing rules
  that implementations must preserve.
- [`docs/lifecycles.md`](docs/lifecycles.md) walks through transaction and worker
  state transitions.
- [`docs/development.md`](docs/development.md) contains environment, migration,
  testing, debugging, and repository-navigation guidance.
- [`docs/database-diagram.html`](docs/database-diagram.html) is a rendered visual
  map of the codebase, schema, tests, and roadmap.
