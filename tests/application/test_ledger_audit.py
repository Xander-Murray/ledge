import pytest

from application.ledger_audit import LedgerAudit, LedgerAuditError


def audit(**changes) -> LedgerAudit:
    values = {
        "transaction_count": 2,
        "active_transaction_count": 2,
        "pending_transaction_count": 0,
        "removed_transaction_count": 0,
        "replaced_transaction_count": 0,
        "journal_count": 2,
        "posting_count": 4,
        "unsealed_journal_count": 0,
        "unbalanced_journal_count": 0,
    }
    return LedgerAudit(**(values | changes))


def test_valid_audit_passes() -> None:
    audit().verify()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"unsealed_journal_count": 1}, "unsealed"),
        ({"unbalanced_journal_count": 1}, "unbalanced"),
        ({"posting_count": 3}, "two postings"),
        ({"active_transaction_count": 1}, "status counts"),
        ({"pending_transaction_count": 3}, "Pending transaction"),
    ],
)
def test_audit_rejects_ledger_invariant_failures(changes, message) -> None:
    with pytest.raises(LedgerAuditError, match=message):
        audit(**changes).verify()
