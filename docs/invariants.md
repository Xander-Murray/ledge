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
expense:unclassified      +1000  (debit)
financial:<account id>    -1000  (credit)
                           -----
                               0
```

For a $5 refund, the provider amount is `-500`, so both posting signs reverse.
The same purchase convention works for a credit card: the financial posting
credits the card liability while the expense is debited.

## How these will be enforced

- Domain value objects will reject non-integer amounts and invalid versions.
- `assert_balanced` will reject an invalid posting set before persistence.
- Later, database constraints will guard against duplicate versions and invalid
  mutations even if application validation is bypassed.
- Application operations will avoid committing internally so a future sync-page
  transaction can roll back as one unit.
