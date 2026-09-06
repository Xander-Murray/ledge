# Domain and ledger model

This document explains the financial objects independently of FastAPI,
PostgreSQL, Plaid, and AWS. The domain layer answers one question: given what the
provider says happened, what auditable accounting history should Ledge create?

## Mental model

```text
Transaction        The provider's current description of an activity
TransactionRemoval The identity of an activity the provider removed
Posting            One signed financial effect
JournalEntry       One immutable group of balanced postings
LedgerState        An in-memory learning and testing snapshot
```

The provider projection answers what appears to be true now. Journal history
answers how Ledge reached that state. Those are deliberately separate concerns.

## Transaction

A `Transaction` is a provider fact normalized into Ledge-owned fields:

```text
account_id                       Ledge financial-account UUID
provider_transaction_id          stable identity from the provider
amount_cents                     signed integer minor units
description                      provider description
is_pending                       whether the activity is an authorization
pending_provider_transaction_id  posted transaction's optional pending source
```

Ledge owns internal UUIDs for database relationships. It does not reinterpret or
replace provider identifiers used for synchronization and duplicate detection.

## Money and postings

All money is represented in integer cents. Binary floating point never enters the
domain or database.

Ledge uses this sign convention:

- Positive provider amount: money spent
- Negative provider amount: money received
- Positive posting: debit
- Negative posting: credit

A $12.50 purchase produces:

```text
suspense:unclassified    +1250
financial:<account id>   -1250
                          -----
                              0
```

The exact same mechanism represents income or a refund because a negative
provider amount reverses both posting signs.

`suspense:unclassified` is intentionally neutral. Transaction synchronization
cannot safely determine whether arbitrary activity is an expense, income,
transfer, payment, or refund. Classification can be added as a separate read-model
concern without weakening the ledger.

## Journal entries

A `JournalEntry` groups at least two postings whose signed amounts sum to zero.
It records:

- A Ledge-owned journal UUID
- The source provider transaction identity
- A human-readable description
- An immutable tuple of postings
- An optional link to the journal it reverses

Corrections append history. They never edit or delete the original journal.

```text
original provider fact
  -> original journal

corrected provider fact
  -> reversal of original journal
  -> replacement journal
```

PostgreSQL repeats the domain balance check when a journal is sealed, then rejects
updates or deletion of sealed journals and postings.

## Removal and refund are different

A removal says the earlier provider fact is no longer valid. Ledge creates a
linked reversal that cancels the active journal's postings.

A refund is normally a new provider transaction with a negative amount. It gets
its own journal entry and remains independently auditable.

## Pending becomes posted

A provider commonly assigns different IDs to an authorization and its final
posted transaction:

```text
pending-1 for $20
  -> original $20 journal

posted-1 for $23 references pending-1
  -> reverse pending-1's $20 journal
  -> append posted-1's $23 journal
  -> retain both provider projections
```

The pending row becomes `replaced`, the posted row becomes `active`, and the net
effect is $23. If the provider separately reports `pending-1` as removed, the
synchronizer recognizes that its reversal already occurred.

## LedgerState and PostgreSQL

`LedgerState` is an immutable in-memory model containing current transactions,
journal entries, removed IDs, and replaced pending IDs. It remains useful because
pure functions and tests can demonstrate lifecycle behavior without any I/O.

It is not a database table and does not represent the deployed storage design.
PostgreSQL distributes the durable state across:

```text
external_transactions  mutable current provider projection
journal_entries        immutable accounting events after sealing
postings               ordered debit and credit lines
```

Repository operations load only the rows needed for a transition, call the same
domain journal factories, and commit the projection plus history atomically.

## Where decisions live

```text
src/domain/models.py       Valid immutable values
src/domain/invariants.py   Posting balance validation
src/domain/ledger.py       Journal creation and lifecycle decisions
src/persistence/repository.py
                           Translation into durable database rows
```

The domain has no imports from the ORM, web framework, provider SDK, or cloud SDK.
See `lifecycles.md` for complete transaction and worker transition examples, and
`invariants.md` for the rules every implementation must preserve.
