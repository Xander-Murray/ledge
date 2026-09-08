from unittest.mock import Mock
from uuid import UUID

import pytest

from application.synchronization import SyncResult, TransactionSynchronizer
from commands.plaid_sync import (
    PlaidAccountCatalogError,
    _cursor_status,
    _ignored_account_ids,
    _parse_arguments,
    _run_cycle,
)
from providers.plaid_sandbox import PlaidAccount

MAPPED_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def test_cycle_reports_committed_sync_without_claiming_a_ledger_audit(capsys) -> None:
    synchronizer = Mock(spec=TransactionSynchronizer)
    synchronizer.synchronize.return_value = SyncResult(
        starting_cursor="first",
        ending_cursor="second",
        pages_fetched=1,
        added_count=0,
        modified_count=0,
        removed_count=0,
    )

    _run_cycle(
        cycle=1,
        cycle_count=1,
        user_id=MAPPED_ID,
        item_id="item-1",
        synchronizer=synchronizer,
    )

    synchronizer.synchronize.assert_called_once_with(
        user_id=MAPPED_ID,
        provider_name="plaid",
        provider_connection_id="item-1",
    )
    output = capsys.readouterr().out
    assert "+0 ~0 -0" in output
    assert "advanced and saved" in output
    assert "PASS" not in output


def account(provider_id: str, account_type: str, subtype: str) -> PlaidAccount:
    return PlaidAccount(provider_id, provider_id, account_type, subtype)


def test_catalog_explicitly_ignores_only_unsupported_accounts() -> None:
    ignored = _ignored_account_ids(
        (
            account("checking", "depository", "checking"),
            account("loan", "loan", "student"),
            account("investment", "investment", "brokerage"),
        ),
        {"checking": MAPPED_ID},
    )

    assert ignored == {"loan", "investment"}


def test_catalog_rejects_new_supported_account_without_mapping() -> None:
    with pytest.raises(PlaidAccountCatalogError, match="has not been mapped"):
        _ignored_account_ids(
            (
                account("checking", "depository", "checking"),
                account("new-card", "credit", "credit card"),
            ),
            {"checking": MAPPED_ID},
        )


def test_catalog_rejects_stale_mapping() -> None:
    with pytest.raises(PlaidAccountCatalogError, match="missing"):
        _ignored_account_ids((), {"checking": MAPPED_ID})


def test_continuous_run_arguments_are_bounded() -> None:
    arguments = _parse_arguments(
        ["--iterations", "5", "--interval", "0.25", "--refresh-between"]
    )

    assert arguments.iterations == 5
    assert arguments.interval == 0.25
    assert arguments.refresh_between is True


@pytest.mark.parametrize(
    "arguments",
    [
        ["--iterations", "0"],
        ["--interval", "-1"],
    ],
)
def test_continuous_run_rejects_invalid_bounds(arguments) -> None:
    with pytest.raises(SystemExit):
        _parse_arguments(arguments)


def test_cursor_status_explains_each_transition() -> None:
    assert _cursor_status(None, "first") == "initialized and saved"
    assert _cursor_status("first", "first") == "unchanged (already current)"
    assert _cursor_status("first", "second") == "advanced and saved"
