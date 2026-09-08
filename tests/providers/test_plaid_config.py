import json

import pytest

from providers.plaid_config import (
    PlaidConfigurationError,
    get_plaid_credentials,
    load_plaid_connection_secret,
)


def test_loads_credentials_without_preserving_outer_whitespace() -> None:
    credentials = get_plaid_credentials(
        {"PLAID_CLIENT_ID": " client-id ", "PLAID_SECRET": " secret "}
    )

    assert credentials.client_id == "client-id"
    assert credentials.secret == "secret"


@pytest.mark.parametrize("missing", ["PLAID_CLIENT_ID", "PLAID_SECRET"])
def test_credentials_are_required(missing: str) -> None:
    environ = {"PLAID_CLIENT_ID": "client", "PLAID_SECRET": "secret"}
    del environ[missing]

    with pytest.raises(PlaidConfigurationError, match=missing):
        get_plaid_credentials(environ)


def test_loads_owner_only_connection_secret(tmp_path) -> None:
    path = tmp_path / "plaid.json"
    path.write_text(
        json.dumps({"access_token": "access-token", "item_id": "item-1"})
    )
    path.chmod(0o600)

    secret = load_plaid_connection_secret(path)

    assert secret.access_token == "access-token"
    assert secret.item_id == "item-1"


def test_rejects_connection_secret_visible_to_other_users(tmp_path) -> None:
    path = tmp_path / "plaid.json"
    path.write_text(
        json.dumps({"access_token": "access-token", "item_id": "item-1"})
    )
    path.chmod(0o644)

    with pytest.raises(PlaidConfigurationError, match="owner-only"):
        load_plaid_connection_secret(path)


def test_rejects_symlinked_connection_secret(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_text(
        json.dumps({"access_token": "access-token", "item_id": "item-1"})
    )
    target.chmod(0o600)
    link = tmp_path / "link.json"
    link.symlink_to(target)

    with pytest.raises(PlaidConfigurationError, match="symlink"):
        load_plaid_connection_secret(link)
