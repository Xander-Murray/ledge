import json
import stat

import pytest

from commands.plaid_sandbox import (
    DEFAULT_INSTITUTION_ID,
    PlaidSandboxConfigurationError,
    _load_config,
    _write_connection_secret,
)


def test_load_config_uses_default_sandbox_institution() -> None:
    config = _load_config(
        {"PLAID_CLIENT_ID": " client ", "PLAID_SECRET": " secret "}
    )

    assert config == {
        "client_id": "client",
        "secret": "secret",
        "institution_id": DEFAULT_INSTITUTION_ID,
    }


@pytest.mark.parametrize("missing", ["PLAID_CLIENT_ID", "PLAID_SECRET"])
def test_load_config_requires_credentials(missing: str) -> None:
    environ = {"PLAID_CLIENT_ID": "client", "PLAID_SECRET": "secret"}
    del environ[missing]

    with pytest.raises(PlaidSandboxConfigurationError, match=missing):
        _load_config(environ)


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
