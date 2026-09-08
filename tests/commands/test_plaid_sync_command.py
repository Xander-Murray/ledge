from uuid import UUID

import pytest

from commands.plaid_sync import (
    PlaidAccountCatalogError,
    _cursor_status,
    _ignored_account_ids,
    _parse_arguments,
)
from providers.plaid_sandbox import PlaidAccount

MAPPED_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


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
