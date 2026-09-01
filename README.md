# Ledge

Ledge is a learning-first financial transaction reconciliation system. It turns
unreliable provider updates into a trustworthy, auditable ledger and will
eventually power a simple consumer cash-flow view.

The user-facing question is straightforward:

> How much money can I safely spend after pending activity and upcoming bills?

The engineering problem underneath it is harder:

> How do we produce correct financial state when transactions are delivered
> twice, modified, removed, retried, or interrupted halfway through processing?

Ledge is primarily a backend, data, and infrastructure portfolio project. The
event pipeline, reconciliation rules, database guarantees, failure recovery, and
AWS architecture are the main work. A future dashboard exists to make that work
visible and give it a concrete consumer use case.

## Project status

Ledge currently has a tested pure-Python domain layer and a reproducible
PostgreSQL persistence layer. The repository now persists additions and
modifications, and removals atomically.

Implemented now:

- Integer-cent money values with runtime validation
- Balanced double-entry postings
- Immutable journal entries
- Immutable in-memory ledger state
- Duplicate-safe transaction additions
- Transaction modification through reversal and replacement
- Transaction removal through reversal without erasing audit history
- Explicit conflict, missing-transaction, and invalid-state errors
- Unit, lifecycle, and complete feed-scenario tests
- Docker Compose PostgreSQL development and test databases
- SQLAlchemy mappings for users, financial accounts, external transactions,
  journal entries, and postings
- Five Alembic migrations that build and remove the complete current schema
- Database-enforced journal sealing, minimum posting count, zero balance, and
  immutability after sealing
- Real PostgreSQL integration coverage for connection, migration round trips,
  schema drift, journal validation, and mutation rejection
- Repository-managed additions with sequential duplicate idempotency and explicit
  conflicting-payload rejection
- Repository-managed modifications that append sealed reversal and replacement
  journals while updating the current external projection
- Repository-managed removals that accept provider identity-only events and append
  one reversal without deleting history
- Injected failure coverage proving partially flushed additions, modifications,
  and removals roll back entirely
- Provider-owned sync-page contracts and a deterministic JSON-backed fake provider
- Durable per-user provider-connection sync state with a nullable initial cursor

Not implemented yet:

- An application service that processes complete updates and advances the cursor
- Transaction versions, raw provider events, or stale-update protection
- Pending-to-posted replacement
- FastAPI, authentication, Plaid, or a dashboard
- S3, SQS, Lambda, EC2, a dead-letter queue, or CloudWatch telemetry

This boundary is intentional. The financial rules and durable database
guarantees are established before provider, network, or cloud behavior is
introduced.

## What Ledge is building

At completion, Ledge will:

1. Receive a webhook indicating that provider data changed.
2. Archive the exact raw event for diagnosis and replay.
3. Queue synchronization work instead of processing it in the HTTP request.
4. Fetch incremental transaction changes using a provider cursor.
5. Reconcile added, modified, removed, and pending-to-posted transactions.
6. Record immutable, balanced journal history in PostgreSQL.
7. Commit transaction changes and cursor advancement atomically.
8. Expose current transactions, balances, sync health, and cash-flow calculations.
9. Retry transient failures and move poison messages to a dead-letter queue.
10. Allow controlled, idempotent replay without duplicating financial effects.

Ledge never moves money. Version 1 is read-only and uses sandbox data.

## Core mental model

The current domain has five main objects:

```text
Transaction     What the provider says happened
Removal         Which provider transaction disappeared from which account
Posting         One signed account-level financial effect
JournalEntry    Ledge's immutable record of one accounting event
LedgerState     The current in-memory transactions and complete journal history
```

### Transaction

A `Transaction` is a provider fact translated into Ledge's language:

```text
provider transaction ID: txn-123
financial account ID:    checking-1
amount:                  1250 cents
description:             Neighborhood Market
```

Provider transaction IDs are used to detect duplicate delivery. The provider
controls their format; Ledge controls its own internal UUIDs.

### Transaction removal

A `TransactionRemoval` contains only an account ID and provider transaction ID.
Providers do not resend the removed transaction's amount or description, so Ledge
loads that last-known data from PostgreSQL before reversing its active journal.

### Posting

A `Posting` is one line of a journal entry. Ledge uses this sign convention:

- Positive posting: debit
- Negative posting: credit
- Positive provider amount: money spent
- Negative provider amount: money received

A $12.50 purchase currently produces:

```text
suspense:unclassified    +1250
financial:<account id>   -1250
                          -----
                              0
```

Ledge intentionally uses `suspense:unclassified` for now. The reconciliation
engine cannot infer whether every transaction is an expense, income, transfer, or
refund. Detailed categorization is not required for the backend MVP.

### Journal entry

A `JournalEntry` groups the postings for one accounting event:

```text
Journal entry ID:       Ledge-owned UUID
Source transaction ID: provider-owned ID
Description:           event context
Postings:              immutable balanced tuple
Reversal of:           optional earlier journal-entry UUID
```

Every journal entry contains at least two postings and sums to zero. Entries are
immutable: corrections append new entries instead of rewriting history.

### Ledger state

`LedgerState` is the current in-memory snapshot:

```text
LedgerState
├── transactions_by_provider_id
├── journal_entries
└── removed_provider_transaction_ids
```

The transaction mapping answers what the provider currently says. Journal entries
answer how Ledge reached that state. Removed IDs remain linked to their original
transactions so deletion does not erase history.

`LedgerState` remains a learning and testing model. PostgreSQL is now the durable
state for repository-managed additions, modifications, and removals, so persistence code
queries the relevant records instead of loading one enormous state object.

## Current state transitions

The domain layer treats each provider update as a state transition:

```text
current state + incoming transaction event = resulting state
```

All functions return a new state. They do not mutate their input.

### Added

`apply_transaction_added` has three outcomes:

```text
Unknown provider ID
  -> store transaction
  -> create one journal entry

Known ID with identical data
  -> harmless duplicate
  -> return existing state

Known ID with different data
  -> raise TransactionConflictError
```

The conflict protects the added-event contract. Legitimate changes must use the
modified transition rather than silently overwriting history.

### Modified

Suppose a $12.50 authorization becomes a final $14.00 transaction:

```text
Journal A: original $12.50 effect
Journal B: reversal of Journal A
Journal C: replacement $14.00 effect
```

The current transaction mapping changes to $14.00, but all three journal entries
remain. Modification requires separate UUIDs for its reversal and replacement.

An identical repeated modification returns the current state unchanged.

### Removed

Removal does not delete the transaction:

```text
Journal A: original effect
Journal B: reversal of Journal A
removed IDs: includes the provider transaction ID
```

The net financial effect becomes zero while the audit history remains. A repeated
removal is harmless and does not create a second reversal.

### Reversal versus refund

A reversal means Ledge's earlier accounting effect is no longer valid. It negates
every posting and links to the entry being reversed.

A refund is usually a separate provider transaction and therefore gets its own
journal entry. Ledge only reverses an earlier transaction when the provider says
that transaction was removed, replaced, or corrected.

## Complete scenario currently tested

`tests/scenarios/test_transaction_feed.py` exercises the current domain as a
small provider feed:

```text
Add a $12.50 grocery transaction
-> deliver the same addition twice
-> add a $5 refund
-> modify the grocery transaction to $14
-> remove the refund
-> deliver the same removal twice
```

The test verifies current transaction state, removed IDs, journal ordering,
reversal relationships, duplicate safety, balance of every entry, and the final
net effect:

```text
suspense:unclassified    +1400
financial account        -1400
```

The refund and its reversal cancel. The original grocery amount and its reversal
cancel. Only the corrected $14 grocery effect remains.

## Correctness rules

These are the long-term invariants Ledge is designed around:

1. Every journal entry balances to zero.
2. Money uses integer minor units, never binary floating point.
3. A provider event or transaction version is applied at most once.
4. Journal entries are immutable; corrections use reversal entries.
5. A synchronization page never partially commits.
6. A cursor advances only after all changes in its transaction commit.
7. Duplicate webhooks and queue deliveries produce the same final state.
8. Removed transactions remain represented in audit history.
9. Every query is scoped to its authenticated user.
10. Raw provider events are retained for debugging and controlled replay.

The pure domain currently proves the first, second, fourth, and eighth rules and
basic duplicate behavior. PostgreSQL independently enforces balanced, sealed,
immutable journal history. Repository and provider phases will enforce atomic
transition, version, cursor, and delivery guarantees.

See `docs/invariants.md` for additional detail.

## Current architecture

```text
Domain tests                         Repository integration
     |                                      |
     v                                      v
Pure state transitions                Transaction
     |                                      |
     v                                      v
New LedgerState                 Domain journal factories
                                            |
                                            v
                                    LedgerRepository
                                            |
                                            v

PostgreSQL 16
  users -> financial_accounts -> external_transactions
                                      |
                                      v
                                journal_entries
                                      |
                                      v
                                   postings

Alembic installs the schema, constraints, and sealing triggers.
`LedgerRepository` connects domain journals to PostgreSQL for additions,
modifications, and removals.
```

The domain has no imports from a web framework, ORM, cloud SDK, or provider SDK.
That separation is deliberate: FastAPI and Lambda will call the domain; the
domain will never know they exist.

## Target architecture

```text
Plaid Sandbox webhook
        |
        v
FastAPI receiver on EC2 ----> S3 immutable raw-event archive
        |
        v
       SQS ---- repeated failures ----> dead-letter queue
        |                                  |
        v                                  v
Lambda synchronization worker       controlled replay
        |
        +----> Plaid /transactions/sync
        |
        v
PostgreSQL ledger, sync state, and read models
        |
        v
FastAPI query endpoints on EC2
        |
        v
Small consumer dashboard

Logs, metrics, and alarms ----> CloudWatch
```

Each AWS service has a specific responsibility:

- **EC2:** Runs the long-lived webhook receiver and query API.
- **S3:** Retains exact raw provider events for audit, debugging, and replay.
- **SQS:** Buffers work and separates quick webhook acceptance from slower sync.
- **Lambda:** Processes queued synchronization work with retry-safe handlers.
- **Dead-letter queue:** Isolates repeatedly failing messages for diagnosis.
- **CloudWatch:** Exposes processing results, latency, retry counts, and failures.
- **PostgreSQL/RDS:** Provides transactions, constraints, atomic commits, and
  durable audit history.

AWS is not being added for decoration. The project should demonstrate why each
service exists, how it fails, and how correctness survives retries.

## How persistence changes the design

The current in-memory concepts map to the implemented database tables as follows:

```text
transactions_by_provider_id     -> external_transactions
journal_entries                 -> journal_entries
JournalEntry.postings           -> postings
removed transaction IDs         -> transaction status/history
future applied versions/events  -> transaction_versions / inbound_events
future cursor                   -> sync_state
```

Journal entries use a draft-and-seal lifecycle:

```text
Create unsealed journal entry
-> insert at least two postings
-> set sealed_at
-> PostgreSQL verifies the postings sum to zero
-> PostgreSQL rejects later journal or posting mutations
```

The domain validates journal balance before persistence. PostgreSQL repeats this
critical validation as the final durable-data boundary, including when a future
bug or alternate writer bypasses the normal Python path.

Applying an addition, modification, or removal through the repository follows:

```text
Begin database transaction
-> look up provider transaction identity and current projection
-> insert or update the current provider projection
-> insert immutable journal entry or reversal
-> insert all balanced postings
-> commit everything together
```

If any step fails, PostgreSQL rolls back the entire operation. Repository tests
inject failures between draft insertion and sealing and prove both that a failed
addition leaves no rows and that a failed removal leaves the original transaction
active without a partial reversal. Cursor updates will join this same transaction
boundary when synchronization is implemented.

The implemented persistence stack is:

- **PostgreSQL:** Durable relational storage and transaction guarantees
- **SQLAlchemy:** Python persistence mapping and database operations
- **Alembic:** Versioned schema migrations for clean creation and upgrades
- **pytest fixtures:** Real PostgreSQL integration, migration, idempotency, and
  injected rollback tests

Pure domain functions remain independent. The repository calls their journal and
reversal factories, then translates the results into SQLAlchemy models.

## Development setup

Requirements:

- Python 3.12 or newer
- Docker with Docker Compose for the PostgreSQL persistence phase

Create the environment and install development dependencies:

```bash
python -m venv venv
venv/bin/pip install -e '.[dev]'
```

Start the local development and test databases:

```bash
docker compose up -d postgres
export LEDGE_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge'
export LEDGE_TEST_DATABASE_URL='postgresql+psycopg://ledge:ledge_local@localhost:5432/ledge_test'
```

The credentials in `compose.yaml` are local development values, not production
secrets. `.env.example` documents the required variables, while `.env` remains
ignored. Docker provides only PostgreSQL; the Python application still runs in
the local virtual environment.

Run the test suite:

```bash
venv/bin/pytest
```

Run tests with detailed failures and local variables:

```bash
venv/bin/pytest -vv -x --tb=long --showlocals
```

Run the complete feed scenario:

```bash
venv/bin/pytest -vv tests/scenarios/test_transaction_feed.py
```

Run database integration tests after PostgreSQL is healthy:

```bash
venv/bin/pytest -m integration -vv
```

Run coverage:

```bash
venv/bin/pytest --cov=domain --cov-report=term-missing
```

Run lint and formatting checks:

```bash
venv/bin/ruff check .
venv/bin/ruff format --check .
```

Expected generated directories such as `venv/`, `__pycache__/`, `.pytest_cache/`,
`.ruff_cache/`, and `*.egg-info/` are ignored and should not be committed.

## Repository map

```text
README.md                         Project orientation and operating guide
docs/architecture.md              Architecture notes
docs/invariants.md                Financial and processing guarantees
docs/lifecycles.md                Transaction lifecycle examples
src/domain/models.py              Transaction, Removal, Posting, JournalEntry, State
src/domain/invariants.py          Shared balance validation
src/domain/ledger.py              Journal factories and state transitions
src/persistence/database.py       Engine and session-factory configuration
src/persistence/models.py         SQLAlchemy persistence mappings
src/persistence/repository.py     Atomic add, modify, and remove persistence
src/providers/base.py             Provider-independent page and protocol contracts
src/providers/fake.py             Deterministic JSON-backed provider adapter
migrations/versions/              Ordered PostgreSQL schema and trigger changes
tests/domain/test_ledger.py       Posting, journal, and addition behavior
tests/domain/test_models.py       Model invariants and immutable state
tests/domain/test_reversals.py    Reversal behavior
tests/domain/test_transitions.py  Modified and removed lifecycles
tests/persistence/                Database, trigger, repository, and rollback tests
tests/providers/                  Provider contract, fixture, and pagination tests
tests/scenarios/                  End-to-end in-memory feed scenarios
```

## Known limitations

The current project is a deliberately bounded persistence checkpoint:

- Persisted addition, modification, and removal have success, idempotency, invalid
  input or state, and rollback coverage appropriate to each operation.
- The same provider ID with different data is detectable, but Ledge cannot yet
  determine whether that data is newer or stale.
- There is no provider event ID, transaction version, or applied-page history.
- Sync cursors can be stored, but no service advances them with ledger changes yet.
- Pending-to-posted replacement is documented but intentionally deferred.
- `suspense:unclassified` is a neutral offset, not a categorization system.
- There is one currency convention. User/account ownership exists in the schema,
  but authentication and user-scoped query services do not.
- There is no API, authentication, webhook verification, or secret storage.

The most important current limitation is event ordering. If Ledge stores version
3 of a transaction and later receives an old version 2 payload, it only sees
different data and may treat it as a new modification. Future version/event
identity and cursor processing must distinguish duplicate, newer, and stale data.

## Roadmap

### 1. Pure in-memory ledger - complete

- Balanced postings
- Immutable journal history
- Added, modified, and removed transitions
- Duplicate-safe repeated delivery
- Complete feed-scenario verification

### 2. PostgreSQL persistence - in progress

- [x] SQLAlchemy persistence models
- [x] Alembic schema migrations
- [x] Journal and posting tables
- [x] Current external-transaction projection
- [x] Unique provider transaction identities within a user
- [x] Database-enforced journal balance and immutability
- [x] Repository translation for additions, modifications, and removals
- [x] Atomic addition, modification, and removal commits
- [x] Sequential duplicate-safe additions and conflict rejection
- [x] Injected addition failure and complete rollback proof
- [x] Persisted removal with duplicate, conflict, and rollback coverage
- [x] Complete persisted-modification edge-case and rollback coverage
- [x] Durable provider-connection sync state and cursor storage
- [ ] Provider transaction versions and stale-update protection

### 3. Fake provider synchronization

- [x] `TransactionProvider` protocol owned by Ledge
- [x] Normalized JSON fixtures loaded at the provider boundary
- [x] Added, modified, removed, empty, and multi-page provider responses
- [x] PostgreSQL sync-state schema with an initial nullable cursor
- [ ] Synchronization service that applies a complete update
- [ ] Cursor state committed with transaction changes
- [ ] Retry after an injected synchronization failure
- [ ] Pending-to-posted fixtures once the base sync pipeline works

### 4. Local FastAPI application

- Webhook acceptance endpoint
- Account, transaction, and sync-status read endpoints
- Explicit request and response schemas
- Fast webhook response independent of synchronization duration
- User ownership enforcement before multi-user reads

### 5. Plaid Sandbox

- Sandbox Link flow and token exchange
- `/transactions/sync` cursor pagination
- Provider payload translation into Ledge domain events
- Added, modified, removed, and pending-to-posted reconciliation
- Webhook signature verification
- No provider access tokens in logs, fixtures, responses, or Git history

### 6. AWS event pipeline

- FastAPI deployment on EC2
- Raw-event archival in S3
- Work queue and dead-letter queue in SQS
- Lambda synchronization worker
- Controlled idempotent replay
- CloudWatch logs, metrics, alarms, and correlation identifiers
- Failure injection for retries, poison messages, and partial batches

### 7. Consumer read models and interface

- Current balances and transaction history
- Sync health and stale-data warnings
- Deterministic recurring-charge detection
- Thirty-day cash-flow projection
- Transparent safe-to-spend calculation
- Small desktop and mobile dashboard

## Version 1 scope

The intended first complete version supports:

- One Plaid Sandbox user
- Checking, savings, and credit accounts
- USD integer cents
- Added, modified, removed, and pending-to-posted activity
- Duplicate-safe incremental synchronization
- Immutable ledger and event history
- Retry, dead-letter, and controlled replay behavior
- Basic recurring-charge and thirty-day cash-flow views
- A transparent safe-to-spend estimate

Explicit non-goals:

- Real customer bank credentials or production Plaid access
- Payments, transfers, or money movement
- Investment accounts
- Multiple currencies
- Machine-learning predictions
- Kubernetes or many independently deployed microservices
- A mobile application
- Complex budgeting, social, or merchant-category features

## Engineering and resume story

The strongest result is not "built a finance dashboard." It is:

> Built an event-driven financial-data pipeline that reconciles duplicate,
> modified, removed, and retried transaction updates into immutable, balanced
> ledger history.

The project should eventually provide concrete examples for discussing:

- At-least-once delivery and why duplicates are expected
- Idempotency at event, transaction-version, and database boundaries
- Reversals instead of mutable financial history
- Atomic database transactions and cursor advancement
- SQS visibility timeouts, retries, partial failures, and dead-letter queues
- Exact raw-event retention in S3 and controlled replay
- Lambda handler design and concurrency
- Structured logs, metrics, alarms, and operational diagnosis
- Provider-token security and webhook verification
- Measured behavior under injected failures rather than unsupported scale claims

Do not claim production scale, latency, reliability, or cost improvements until
they have been measured and the test setup is documented.

## Working principles

- Add one concept at a time and understand why it exists.
- Write an invariant or failing test before implementing behavior.
- Keep domain logic independent of frameworks and infrastructure.
- Use provider documentation as the source of truth for provider behavior.
- Treat duplicate delivery and retries as normal conditions.
- Never hide failures behind stale success-shaped data.
- Commit coherent concepts and keep the project runnable at each checkpoint.
- Keep only code that can be explained line by line.

## Further reading

- `docs/architecture.md` - architecture boundaries and transaction flow
- `docs/invariants.md` - correctness requirements and sign conventions
- `docs/lifecycles.md` - modification, removal, and future pending lifecycles

The immediate next task is the synchronization service that uses the durable
cursor. Do not add FastAPI, Plaid, or AWS until provider-style pages can apply
added, modified, and removed events atomically and tests prove failures leave no
partial ledger or cursor state.
