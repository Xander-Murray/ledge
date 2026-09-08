import json
import stat

import pytest

from commands.plaid_sandbox import (
    _parse_arguments,
    _write_connection_secret,
    main,
)
from providers.plaid_config import PlaidConfigurationError


def test_bootstrap_rejects_empty_institution_before_connecting(monkeypatch) -> None:
    monkeypatch.setenv("PLAID_CLIENT_ID", "client")
    monkeypatch.setenv("PLAID_SECRET", "secret")
    monkeypatch.setenv("PLAID_INSTITUTION_ID", " ")

    with pytest.raises(PlaidConfigurationError, match="PLAID_INSTITUTION_ID"):
        main([])


def test_connection_secret_is_created_with_owner_only_permissions(tmp_path) -> None:
    token_file = tmp_path / "private" / "plaid.json"

    _write_connection_secret(
        token_file,
        access_token="access-sandbox-token",
        item_id="item-1",
    )

    assert json.loads(token_file.read_text()) == {
        "access_token": "access-sandbox-token",
        "item_id": "item-1",
    }
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600

    with pytest.raises(FileExistsError):
        _write_connection_secret(
            token_file,
            access_token="replacement-token",
            item_id="item-2",
        )

def test_dynamic_profile_can_use_isolated_identity_and_token_file(tmp_path) -> None:
    user_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    token_file = tmp_path / "dynamic.json"

    arguments = _parse_arguments(
        [
            "--dynamic-transactions",
            "--user-id",
            user_id,
            "--token-file",
            str(token_file),
        ]
    )

    assert arguments.dynamic_transactions is True
    assert str(arguments.user_id) == user_id
    assert arguments.token_file == token_file
