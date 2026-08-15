# Transaction lifecycles

These examples describe behavior to implement and test during Phase 1.

## Pending purchase becomes posted

```text
pending `pending-1` v1 added for 2,000 cents
  -> purchase journal: expense +2000 / financial account -2000
posted `posted-1` v1 arrives with pending_transaction_id=`pending-1`
  -> reversal linked to pending purchase: expense -2000 / account +2000
  -> posted journal: expense +2000 / account -2000
  -> pending transaction marked replaced by `posted-1`
```

The history contains three balanced entries, while the net consumer effect is one
$20 purchase.

## Posted amount changes

```text
`txn-1` v1 added for 1,000 cents
  -> original journal: expense +1000 / account -1000
`txn-1` v2 modifies amount to 1,200 cents
  -> reversal of v1: expense -1000 / account +1000
  -> replacement for v2: expense +1200 / account -1200
```

The net effect is $12, and both provider versions remain inspectable.

## Posted transaction is removed

```text
`txn-1` v1 added for 1,000 cents
  -> original journal: expense +1000 / account -1000
`txn-1` v2 removed
  -> reversal of active effect: expense -1000 / account +1000
  -> current transaction marked removed
```

No history is deleted. Re-delivering removal v2 creates nothing new.

## Failure and recovery scenarios

### Failure after the first change in a sync page

The page transaction rolls back, including journal rows, transaction versions,
and cursor state. A retry begins from the old cursor and reapplies the page.

### Duplicate delivery

Event-level idempotency should stop redundant work when possible. Unique
transaction-version identities provide a second boundary that prevents duplicate
journal effects if both deliveries reach the ledger.

### Invalid unbalanced journal

Domain validation rejects it before persistence. Later, PostgreSQL should also
reject it if application validation is bypassed. No partial journal remains.
