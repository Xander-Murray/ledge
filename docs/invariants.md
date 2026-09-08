# Ledger invariants

These are correctness requirements, not aspirations. A change that violates one
of them must be rejected or rolled back.

1. **Every journal entry balances to zero.** The sum of its signed postings is
   exactly zero. Positive postings are debits and negative postings are credits.
2. **Money uses integer minor units.** USD values are stored and calculated as
   cents. Binary floating-point values never enter the domain model or database.
3. **A provider notification is accepted once per connection.** Re-delivering an
   identical provider event ID returns its existing inbox row; changed data under
   that identity is rejected. Transaction-version ordering remains a future rule.
4. **Journal entries are immutable.** Corrections create a linked reversal and a
   replacement entry instead of editing history.
5. **A synchronization batch never partially commits.** All fetched pages and the
   final cursor update share one database transaction.
6. **A cursor advances only after its batch commits.** Failed batches retain the
   last successfully committed cursor.
7. **Duplicate delivery is harmless.** Repeating an event, fetched batch, or
   transaction update leads to the same final ledger state.
8. **Removed transactions remain auditable.** Removal reverses the active effect;
   it does not erase transaction or journal history.
9. **Queries are scoped to the selected user identity.** A request cannot read
   another user's financial data. Today that identity is deployment configuration;
   a future multi-user version must derive it from verified authentication.
10. **Submitted provider payloads are retained.** The accepted payload object is
    kept in PostgreSQL for diagnosis and future controlled replay.
11. **A pending transaction has at most one posted replacement.** The pending
    effect is reversed once, and the posted effect becomes the active truth.
12. **Only the current event claim may finalize work.** Each attempt owns a unique
    token; an expired worker cannot overwrite a newer worker's result.
13. **Provider calls do not hold the inbox claim transaction open.** Claiming and
    finalization use short transactions around slower synchronization work.
14. **Every included provider account is understood.** Supported Plaid accounts
    require a durable mapping; unsupported types are explicitly ignored. Unknown
    supported accounts and missing mapped accounts stop synchronization.
15. **Secrets stay outside domain and ledger rows.** Plaid credentials come from
    environment configuration, and local Item tokens must be owner-readable only.

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
- Persisted removal accepts the account and transaction identities providers
  supply, verifies the account against the stored projection, appends one reversal
  of the active journal, and atomically marks the projection removed. Identical
  redelivery creates no new history.
- Persisted pending replacement keeps both provider identities, links the posted
  row to its pending source, reverses the pending journal exactly once, and writes
  the posted journal in the same transaction. PostgreSQL prevents two posted rows
  from claiming one pending identity.
- Each `(provider_name, provider_connection_id)` identifies at most one durable
  sync stream and belongs to one Ledge user. Its cursor may be null only to
  represent a stream that has not completed an initial synchronization.
- A complete provider update and its final cursor share one database transaction.
  The coordinator detects cursor races, rejects pagination loops,
  rolls failed batches back completely, matches pending replacements across page
  boundaries, and can retry from the unchanged cursor.
- Plaid responses are parsed with decimal semantics and normalized to integer
  cents using round-half-even. Only USD activity may cross into the domain.
- Provider-account mappings share a user with both their sync state and financial
  account. Uniqueness constraints prevent one provider account from mapping twice
  within an Item or one Ledge account from being reused by another mapping.
- Plaid pagination mutation is an explicit provider failure. A posted transaction
  whose pending source Plaid compacted away may be imported standalone; missing
  modification and removal targets remain errors.
- Local access-token files reject symlinks and group/other permissions. Provider
  failures do not persist or print response bodies, credentials, or access tokens.
- Each inbound notification is unique within its provider connection. PostgreSQL
  stores the submitted payload and lifecycle state, while intake distinguishes an
  identical redelivery from conflicting reuse of the same provider event ID.
- Processing claims increment an attempt count and store a unique token plus
  lease timestamp. An active claim rejects another worker; an expired claim can be
  reclaimed, and token-checked completion prevents the expired worker from
  changing the newer result.
- Expected failures store only a bounded category. Completion and failure retain
  timestamps for latency and retry measurements without persisting raw exception
  messages.
- Account, transaction, and sync-status SQL queries require the configured user
  UUID. Integration fixtures include another user's rows and prove those rows are
  absent from API responses. Resource schemas also omit `user_id`.
- HTTP query validation constrains transaction status to known lifecycle values,
  `limit` to 1 through 100, and `offset` to a nonnegative integer.

## Planned enforcement

- Provider transaction versions will distinguish newer and stale updates;
  version-aware ordering is not implemented yet.
- SQS visibility timeouts and dead-letter policies will complement, rather than
  replace, the database lease and claim-token guarantees.
- Authentication will replace the configured single-user identity with a verified
  per-request identity. The read-query ownership checks are already in place.
