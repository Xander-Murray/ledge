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

The implemented local vertical slice includes domain models, ledger operations,
PostgreSQL persistence, migrations, deterministic synchronization, FastAPI read
models, durable notification intake, and a leased event processor. It now also
creates a real Plaid Sandbox Item, imports supported accounts, persists provider
mappings, and translates `/transactions/sync` responses through the same
provider-neutral application service. AWS delivery is the next major milestone.

## Current checkpoint non-goals

- Production bank credentials, Plaid Link, or production provider access
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
        |
        v
FastAPI async read endpoints
        |
        v
durable webhook inbox and leased event processor
        |
        v
real Plaid Sandbox account bootstrap and transaction synchronization
```

Starting with pure functions keeps accounting rules easy to understand and test.
The implemented repository persists additions, modifications, removals, and
pending-to-posted replacements without making the domain depend on SQLAlchemy.
It keeps the current provider projection in `external_transactions` and appends
balanced, sealed history to `journal_entries` and `postings`. Plaid Sandbox now
drives this path with actual provider responses; AWS and React remain later phases.

## Current provider boundary

```text
normalized JSON fixture                  Plaid Sandbox API
        |                                       |
        v                                       v
FakeTransactionProvider              PlaidTransactionProvider
        |                                       |
        +-------------------+-------------------+
                            v
TransactionSyncPage
├── added Transaction values
├── modified Transaction values
├── removed TransactionRemoval values
├── next_cursor
└── has_more
```

`TransactionProvider` is a protocol owned by Ledge. The fake implementation makes
pagination and provider changes deterministic. The Plaid implementation calls
`/transactions/sync`, filters explicitly unsupported accounts, translates USD
amounts into integer cents, and returns the same normalized types. The ledger and
synchronization code therefore remain provider-free.

Plaid JSON decimals are parsed without first becoming binary floats, then rounded
to cents with round-half-even at the provider boundary. A supported account that
lacks a stored mapping, or a mapped account that disappears from the Item, fails
closed instead of silently losing financial activity. Provider errors retain a
safe machine error code when available but never include response bodies,
credentials, or access tokens.

The boundary intentionally models removals with only account and transaction
identities. The repository uses those identities to load the last-known amount,
description, and active journal required for the accounting reversal.

## Plaid connection bootstrap

```text
Sandbox public token -> access-token exchange -> /accounts/get
                     -> create/find Ledge user
                     -> import supported financial accounts
                     -> create provider_account_mappings
                     -> create transaction_sync_states row
                     -> save access token in owner-only local file
```

`PlaidSandboxConnector` owns the database portion of setup. Each
`provider_account_mappings` row joins one Plaid account ID to one Ledge financial
account and the Item's sync state, with composite foreign keys enforcing common
user ownership. One provider account can map once per Item, and one Ledge account
cannot be reused by another mapping.

The access token is deliberately not stored in PostgreSQL. For local development,
the bootstrap CLI writes it beneath `.ledge/` with mode `0600`; configuration
loading rejects symlinks or broader permissions. A deployment can replace this
file adapter with a managed secret store without changing domain values.

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

A sync run starts from the stored cursor and fetches every currently available
provider page before opening its write transaction. It then locks and rechecks the
sync-state row, applies the complete fetched update, stores the final cursor, and
commits once. Any exception rolls back both ledger changes and cursor movement.

If provider data changes during pagination, the fetched batch must be discarded
and pagination restarted from the original cursor. If processing fails while
writing the batch, no partial journal writes or new cursor should become visible.
Receiving a webhook twice must be harmless because at-least-once systems naturally
produce duplicate deliveries. Pending replacement links and pending removals may
arrive on separate pages, so the coordinator examines the complete fetched update
before applying either event. Both deterministic and Plaid adapters use this
database boundary. Plaid's mutation-during-pagination response becomes an
explicit retryable provider failure so the entire fetched batch is discarded.

Plaid can compact a pending transaction out of the returned change set while
still returning its posted replacement. In that narrow case, the synchronizer
imports the posted transaction as a standalone fact. Missing transactions during
ordinary modification or removal still fail; the fallback does not hide general
referential errors.

## Current event-processing boundary

The HTTP request and slower synchronization work are separate operations. The
normalized webhook endpoint first resolves a provider connection owned by the
configured user, then inserts an `inbound_events` row. Its unique
`(transaction_sync_state_id, provider_event_id)` identity makes identical
redelivery return the existing row; changed data under the same identity is an
explicit conflict.

```text
POST normalized notification
  -> short async transaction
  -> insert pending event or return identical existing event
  -> 202 Accepted
```

`InboundEventProcessor` handles stored work using three boundaries:

```text
short claim transaction
  -> status=processing, attempt_count++, new processing_token
  -> release database connection

provider fetch and atomic synchronization
  -> no inbox row lock or claim transaction remains open

short finalization transaction
  -> update only when the processing token still matches
  -> status=processed or categorized status=failed
```

An active five-minute lease rejects a duplicate worker. An expired lease can be
reclaimed with a new token and incremented attempt count. The old worker cannot
complete or fail the newer claim because finalization checks both status and
token. This fencing behavior is necessary when an at-least-once queue retries a
message while an earlier invocation is delayed.

The inbox stores processing start and finish timestamps, attempt count, and a
bounded failure category rather than raw exception text. Those fields support
measured processing latency, retry rate, first-attempt success rate, abandoned
work recovery, and failure counts without storing potentially sensitive error
messages. SQS delivery, a Lambda handler, and CloudWatch emission are not wired
yet.

## Current HTTP boundary

```text
Uvicorn -> FastAPI application factory -> configured LEDGE_USER_ID
                                      -> async request session
                                      -> PostgreSQL read query
```

The application factory creates an async SQLAlchemy engine when one is not
injected and disposes its connection pool during FastAPI shutdown. Each request
receives a short-lived async session through dependency injection. The existing
synchronous engine and repository remain the write path for synchronization;
the HTTP layer uses async sessions so database waits do not block the event loop.

The current HTTP surface has four read routes and one durable intake route:

```text
GET /health         service and PostgreSQL readiness
GET /accounts       configured user's checking, savings, and credit accounts
GET /transactions   current provider projection with account/status filters
GET /sync-status    committed cursor state for each provider connection
POST /webhooks/transactions
                    accept a normalized transaction notification
```

`GET /transactions` orders by most recently updated first and bounds offset
pagination to at most 100 rows per request. Removed and replaced rows remain
queryable because visibility is part of the audit story. Resource responses omit
`user_id`, but every SQL statement filters by the UUID loaded from
`LEDGE_USER_ID`. That configured identity is an explicit single-user MVP boundary,
not authentication; `create_app` accepts an injected UUID so a future auth
dependency can replace it without changing route queries.

The webhook route accepts only `transactions.updated`, preserves its submitted
payload object, and returns without running synchronization. It is still a local,
provider-neutral envelope: real Plaid webhook validation, translation, and worker
dispatch remain future responsibilities. All routes map SQLAlchemy failures to
`503` without exposing connection details. Unit tests replace the session
factory, while integration tests migrate and query the real disposable
`ledge_test` PostgreSQL database.
