# Ledge architecture

## Consumer story

As a consumer, I want bank activity to remain trustworthy when a provider sends
duplicates or changes a pending charge, so I can understand what affected my
balance and why without seeing the same purchase counted twice.

## MVP

Ledge will read USD checking, savings, and credit-account transactions for one
sandbox consumer. It will reconcile added, modified, removed, and
pending-to-posted activity into an auditable double-entry ledger. Later phases
add recurring-charge detection, a 30-day projection, and a transparent
safe-to-spend estimate.

The first milestone established domain models, ledger operations, PostgreSQL
persistence, migrations, and automated tests. The current milestone is exposing
those capabilities through a small FastAPI boundary before adding Plaid or AWS.

## Current checkpoint non-goals

- Real bank credentials or production provider access
- Payments, transfers, or any money movement
- Investments, multiple currencies, or machine-learning predictions
- Cloud deployment or independently deployed services during Phase 1
- A native mobile application
- Complex budgeting and social features

## Target architecture

```text
Plaid Sandbox webhook
        |
        v
FastAPI receiver ----> immutable S3 event archive
        |
        v
       SQS ----failures----> DLQ ----> authenticated replay
        |
        v
Lambda sync worker ----> provider /transactions/sync
        |
        v
PostgreSQL ledger and read models
        |
        v
FastAPI queries ----> React dashboard
```

## Current Phase 1 path

```text
Transaction value
        |
        v
pure ledger functions
        |
        v
balanced postings
        |
        v
LedgerRepository
        |
        v
SQLAlchemy models
        |
        v
PostgreSQL constraints, triggers, and migrations
```

Starting with pure functions keeps accounting rules easy to understand and test.
The implemented repository persists additions, modifications, removals, and
pending-to-posted replacements without making the domain depend on SQLAlchemy.
It keeps the current provider projection in `external_transactions` and appends
balanced, sealed history to `journal_entries` and `postings`. Plaid, AWS, and
React remain later phases.

## Current provider boundary

```text
normalized JSON fixture
        |
        v
FakeTransactionProvider
        |
        v
TransactionSyncPage
├── added Transaction values
├── modified Transaction values
├── removed TransactionRemoval values
├── next_cursor
└── has_more
```

`TransactionProvider` is a protocol owned by Ledge. The fake implementation makes
pagination and provider changes deterministic without adding network credentials.
A future Plaid adapter will translate Plaid account and transaction fields into
the same normalized types, leaving ledger and synchronization code provider-free.

The boundary intentionally models removals with only account and transaction
identities. The repository uses those identities to load the last-known amount,
description, and active journal required for the accounting reversal.

## Durable synchronization identity

`transaction_sync_states` stores progress for one provider connection. Its
provider-neutral identity is `(provider_name, provider_connection_id)`, which is
globally unique and owned by one Ledge user. A `NULL` cursor means the connection
has not completed its first synchronization; a non-null cursor is the last
successfully committed provider bookmark.

`TransactionSynchronizer` reads the starting cursor, fetches all available pages
without holding a database lock, then opens one write transaction. It locks and
rechecks the sync-state row, applies every change through `LedgerRepository`, and
stores the final cursor before commit. A changed cursor rejects the stale fetched
batch instead of allowing two workers to apply overlapping updates.

```text
stored cursor -> fetch every page -> identify pending replacements
              -> lock and recheck cursor -> apply lifecycle changes
              -> store final cursor -> commit
```

## Current transaction boundary

The caller opens a SQLAlchemy transaction and passes its session to the
repository. Repository methods may `flush()` SQL so PostgreSQL constraints and
triggers run, but they do not commit. A successful addition, modification,
removal, or pending replacement is committed by the caller; an exception rolls
back its projection, journals, and postings together.

Addition is sequentially idempotent by `(user_id, provider_transaction_id)`.
Identical redelivery returns the existing Ledge UUID without new journal effects;
different data on the added path raises a domain conflict. Modification locks the
current projection, reconstructs its one active journal, appends a reversal and
replacement, updates the projection, and seals both new journals atomically.
Removal uses the same locked active-journal lookup, appends its reversal, and
marks the projection removed. An identical repeated removal is a no-op.

Pending replacement locks the pending projection, reverses its active journal,
creates a separately identified posted projection and journal, and marks the
pending projection replaced. The posted row retains the provider-supplied pending
reference, and a unique database constraint prevents two posted rows from
claiming the same pending transaction.

Integration tests inject failure after draft rows have been flushed but before
sealing. They verify that additions leave no partial rows and removals retain the
original active projection without a partial reversal.

## Current synchronization boundary

A sync run will start from the stored cursor and fetch every currently available
provider page before opening its write transaction. It will then lock and recheck
the sync-state row, apply the complete fetched update, store the final cursor, and
commit once. Any exception will roll back both ledger changes and cursor movement.

If provider data changes during pagination, the fetched batch must be discarded
and pagination restarted from the original cursor. If processing fails while
writing the batch, no partial journal writes or new cursor should become visible.
Receiving a webhook twice must be harmless because at-least-once systems naturally
produce duplicate deliveries. Pending replacement links and pending removals may
arrive on separate pages, so the coordinator examines the complete fetched update
before applying either event. The local fake-provider coordinator now enforces
this database boundary; provider-specific pagination mutation errors will be
handled when the Plaid adapter is introduced.

## Current HTTP boundary

```text
Uvicorn -> FastAPI application factory -> async request session
                                      -> SELECT 1 -> PostgreSQL
```

The application factory creates an async SQLAlchemy engine when one is not
injected and disposes its connection pool during FastAPI shutdown. Each request
receives a short-lived async session through dependency injection. The existing
synchronous engine and repository remain the write path for synchronization;
the HTTP layer uses async sessions so database waits do not block the event loop.

`GET /health` is the first endpoint. It returns `200` only after PostgreSQL
answers a readiness query and maps SQLAlchemy failures to `503` without exposing
connection details. Unit tests replace the session factory at this boundary, and
an integration test executes the same route against `ledge_test`.
