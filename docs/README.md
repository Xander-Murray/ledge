# Ledge project documentation

The root `README.md` is the public product overview. This directory contains the
detailed engineering model used to develop, review, and explain Ledge.

## Current priority

The activity dashboard is implemented at `/`; purchase history lives at
`/activity/{transaction_id}`. It reads the same user-scoped PostgreSQL records as
the API, with no separate frontend service. The next infrastructure checkpoint
is Lambda deployment and an acceptance run through SQS. Authentication,
balances and projections remain unfinished.

Read [demonstration.md](demonstration.md) for the active completion plan and
repeatable evidence commands. SQS publishing is implemented and verified; the
Lambda runtime is committed as `b141fe9`. Deployment awaits hosted PostgreSQL.
Hosting remains undecided; no Neon or RDS database has been confirmed.
The dashboard and connected local Sandbox exercise are implemented and verified.

## Current checkpoint

The local vertical slice currently includes:

```text
normalized webhook intake
  -> durable duplicate-safe PostgreSQL inbox
  -> leased event processor
  -> deterministic or real Plaid Sandbox adapter
  -> cursor-based atomic synchronization
  -> immutable double-entry journals
  -> user-scoped FastAPI reads
```

The current checkpoint has 245 passing tests (verified 2026-10-02) and nine reversible Alembic
migrations. A real Plaid Sandbox Item has been created and synchronized through
the same application boundary used by deterministic tests. The next major
milestone is deployed SQS/Lambda acceptance, followed by repeatable operational
measurements.

A recorded local dynamic-Sandbox run imported 125 transactions as 125 journals
and 250 postings in 1.175 seconds. Later refreshes exercised cursor advancement
and pending replacements in 0.511-0.788 seconds. These values are local Sandbox
evidence, not production benchmarks.

## Reading order

1. `domain-model.md` explains the financial vocabulary and why current provider
   state is separate from immutable journal history.
2. `lifecycles.md` shows what happens when Plaid transactions are added, modified,
   removed, or replaced and when event workers fail or retry.
3. `invariants.md` defines the rules every implementation must preserve.
4. `architecture.md` connects the domain, PostgreSQL, FastAPI, provider boundary,
   event processor, and target AWS design.
5. `development.md` explains setup, Alembic, testing, debugging, and repository
   navigation.
6. `database-diagram.html` provides a rendered visual map of the codebase,
   relational schema, test layers, and roadmap.
7. `commands.md` is the practical command reference for local setup, Sandbox
   synchronization, tests, the API, and PostgreSQL inspection.
8. `demonstration.md` distinguishes real Sandbox integration from controlled
   failure experiments and records the remaining completion work.
9. `deployment.md` records the implemented AWS contracts, required configuration,
   hosting decision and remaining deployment verification.

## Source of truth

Documentation explains intent, but executable behavior is authoritative:

```text
src/domain/              financial rules
src/persistence/models.py current SQLAlchemy schema
migrations/versions/     database evolution
src/application/         synchronization and worker coordination
src/api/                 HTTP contracts
tests/                   verified behavior and failure cases
```

When these disagree with a document, update the document or treat the mismatch as
a design issue rather than silently assuming both are correct.
