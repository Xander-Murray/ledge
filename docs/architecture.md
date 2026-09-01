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

The first milestone focuses on domain models, ledger operations, PostgreSQL
persistence, migrations, and automated tests. Provider payloads will be translated
into Ledge's own domain types at the boundary.

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
The implemented repository persists additions, modifications, and removals
without making the domain depend on SQLAlchemy. It keeps the current provider
projection in `external_transactions` and appends balanced, sealed history to
`journal_entries` and `postings`. Plaid, AWS, FastAPI, and React remain later
phases.

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

The table exists, but no service advances its cursor yet. The next application
checkpoint will fetch all available provider pages, lock the sync-state row,
recheck its starting cursor, apply every ledger change, and store the final cursor
inside one database transaction.

## Current transaction boundary

The caller opens a SQLAlchemy transaction and passes its session to the
repository. Repository methods may `flush()` SQL so PostgreSQL constraints and
triggers run, but they do not commit. A successful addition, modification, or
removal is committed by the caller; an exception rolls back its projection,
journals, and postings together.

Addition is sequentially idempotent by `(user_id, provider_transaction_id)`.
Identical redelivery returns the existing Ledge UUID without new journal effects;
different data on the added path raises a domain conflict. Modification locks the
current projection, reconstructs its one active journal, appends a reversal and
replacement, updates the projection, and seals both new journals atomically.
Removal uses the same locked active-journal lookup, appends its reversal, and
marks the projection removed. An identical repeated removal is a no-op.

Integration tests inject failure after draft rows have been flushed but before
sealing. They verify that additions leave no partial rows and removals retain the
original active projection without a partial reversal.

## Future synchronization boundary

A sync run will start from the stored cursor and fetch every currently available
provider page before opening its write transaction. It will then lock and recheck
the sync-state row, apply the complete fetched update, store the final cursor, and
commit once. Any exception will roll back both ledger changes and cursor movement.

If provider data changes during pagination, the fetched batch must be discarded
and pagination restarted from the original cursor. If processing fails while
writing the batch, no partial journal writes or new cursor should become visible.
Receiving a webhook twice must be harmless because at-least-once systems naturally
produce duplicate deliveries.
