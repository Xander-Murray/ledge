# Transaction lifecycles

These examples describe current and planned lifecycle behavior. The pure domain
supports addition, modification, and removal. PostgreSQL persistence currently
supports all three; provider versions are still planned.

## Pending purchase becomes posted

**Status:** Planned after the base provider synchronization pipeline.

```text
pending `pending-1` v1 added for 2,000 cents
  -> purchase journal: suspense +2000 / financial account -2000
posted `posted-1` v1 arrives with pending_transaction_id=`pending-1`
  -> reversal linked to pending purchase: suspense -2000 / account +2000
  -> posted journal: suspense +2000 / account -2000
  -> pending transaction marked replaced by `posted-1`
```

The history contains three balanced entries, while the net consumer effect is one
$20 purchase.

## Posted amount changes

**Status:** Implemented in the pure domain and PostgreSQL repository. The `v1` and
`v2` labels below explain the desired provider history; explicit version storage
and stale-version rejection are not implemented yet.

```text
`txn-1` v1 added for 1,000 cents
  -> original journal: suspense +1000 / account -1000
`txn-1` v2 modifies amount to 1,200 cents
  -> reversal of v1: suspense -1000 / account +1000
  -> replacement for v2: suspense +1200 / account -1200
```

The net effect is $12, and both accounting states remain inspectable through
journal history. Explicit provider-version records are still planned.

The persisted workflow locks the current external projection, identifies exactly
one active journal, reconstructs it as a domain value, creates a linked reversal
and balanced replacement, updates the projection, flushes both drafts, and seals
them in the caller's database transaction. The original journal remains sealed
and unchanged.

## Posted transaction is removed

**Status:** Implemented in the pure domain and PostgreSQL repository.

```text
`txn-1` v1 added for 1,000 cents
  -> original journal: suspense +1000 / account -1000
`txn-1` v2 removed
  -> reversal of active effect: suspense -1000 / account +1000
  -> current transaction marked removed
```

No history is deleted. Re-delivering removal v2 creates nothing new.

The persisted workflow locks the projection, verifies that the removal payload
matches its latest known data, finds exactly one active journal, appends and seals
its linked reversal, and marks the projection removed in one database transaction.
This also works after modification: removal reverses the replacement journal, not
the obsolete original journal.

## Failure and recovery scenarios

### Failure after the first change in a sync page

**Status:** Repository rollback is proven for addition, modification, and removal;
page and cursor state remain planned. The future page transaction will roll back
journal rows, transaction versions, and cursor state together. A retry will begin
from the old cursor and reapply the page.

### Duplicate delivery

**Status:** Sequential duplicate additions, modifications, and removals are
implemented. An identical payload returns the existing external transaction ID
without another journal; conflicting data is rejected where the event contract
requires an exact match. Concurrent missing-row races and event/version identities
remain future boundaries, with the database uniqueness constraint providing final
provider-identity protection today.

### Invalid unbalanced journal

Domain validation rejects it before persistence. PostgreSQL also rejects sealing
when fewer than two postings exist or their sum is nonzero, then prevents changes
to sealed journal history. No partial journal remains after transaction rollback.
