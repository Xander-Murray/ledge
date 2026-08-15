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

## Non-goals

- Real bank credentials or production provider access
- Payments, transfers, or any money movement
- Investments, multiple currencies, or machine-learning predictions
- Cloud infrastructure, independently deployed microservices, or a mobile app
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

## Phase 1 path

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
SQLAlchemy persistence (later in Phase 1)
        |
        v
PostgreSQL constraints and migrations (later in Phase 1)
```

Starting with pure functions keeps accounting rules easy to understand and test.
Database setup comes after those rules work locally; Plaid, AWS, FastAPI, and
React come in later phases.

## Future transaction boundary

A sync page will eventually open one database transaction, apply every change,
store the next cursor, and commit once. Any exception will roll back the entire
page, including its cursor. Retrying will be safe because each provider
transaction version will have a unique identity.

If processing fails halfway through a page, no partial journal writes or new
cursor should become visible. Receiving a webhook twice must be harmless because
at-least-once systems naturally produce duplicate deliveries.
