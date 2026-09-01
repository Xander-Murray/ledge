# Ledger invariants

These are correctness requirements, not aspirations. A change that violates one
of them must be rejected or rolled back.

1. **Every journal entry balances to zero.** The sum of its signed postings is
   exactly zero. Positive postings are debits and negative postings are credits.
2. **Money uses integer minor units.** USD values are stored and calculated as
   cents. Binary floating-point values never enter the domain model or database.
3. **A provider transaction version is applied at most once.** The pair of
   provider transaction ID and version is unique. Re-delivery must not add another
   financial effect.
4. **Journal entries are immutable.** Corrections create a linked reversal and a
   replacement entry instead of editing history.
5. **A synchronization page never partially commits.** All changes in a page and
   its cursor update share one database transaction.
6. **A cursor advances only after its page commits.** Failed pages retain the last
   successfully committed cursor.
7. **Duplicate delivery is harmless.** Repeating an event, page, or transaction
   version leads to the same final ledger state.
8. **Removed transactions remain auditable.** Removal reverses the active effect;
   it does not erase transaction or journal history.
9. **Queries are scoped to the authenticated user.** Users cannot read or alter
   another user's financial data.
10. **Raw provider events are retained.** Exact payloads are kept for diagnosis
    and controlled replay.

## Sign conventions

Provider amounts are positive when money is spent and negative when money is
received. Ledger postings are positive for debits and negative for credits.

For a $10 checking-account purchase:

```text
suspense:unclassified     +1000  (debit)
financial:<account id>    -1000  (credit)
                           -----
                               0
```

For a $5 refund, the provider amount is `-500`, so both posting signs reverse.
The same purchase convention works for a credit card: the financial posting
credits the card liability while the expense is debited.

## Current enforcement

- Domain value objects reject non-integer amounts, and `assert_balanced` rejects
  fewer than two postings or a nonzero total before persistence.
- PostgreSQL sealing triggers independently require at least two postings and a
  zero total, then prevent updates or deletes of sealed journals and their lines.
- Reversal constraints prevent self-reversal, cross-transaction reversal, and
  reversing the same journal more than once.
- `(user_id, provider_transaction_id)` uniquely identifies the current external
  transaction projection. The repository treats identical sequential additions
  as no-ops and rejects conflicting additions.
- Repository methods flush but do not commit. The caller owns the transaction,
  and injected-failure coverage proves a partially flushed addition rolls back
  without leaving transaction, journal, or posting rows.
- Persisted modification locks the current projection, requires exactly one
  active journal, and atomically appends a reversal and replacement. Identical
  redelivery is a no-op, removed transactions reject modification, and failures
  roll back both the projection update and new journal rows.
- Persisted removal locks the current projection, requires an exact payload match,
  appends one reversal of the active journal, and atomically marks the projection
  removed. Identical redelivery creates no new history.

## Planned enforcement

- Provider event IDs and transaction versions will distinguish duplicate, newer,
  and stale updates; version-aware idempotency is not implemented yet.
- A sync page and its cursor update will share one database transaction.
- Authentication and user-scoped query services will enforce ownership at the API
  boundary; the relational schema already enforces user/account ownership.
