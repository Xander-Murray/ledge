"""Create and persist a real Plaid Sandbox connection."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path

import httpx2

from api.config import get_configured_user_id
from application.plaid_connection import PlaidSandboxConnector
from persistence.database import (
    create_database_engine,
    create_session_factory,
    get_database_url,
)
from providers.plaid_sandbox import PlaidSandboxClient

DEFAULT_INSTITUTION_ID = "ins_109508"
DEFAULT_TOKEN_FILE = Path(".ledge/plaid-sandbox.json")


class PlaidSandboxConfigurationError(RuntimeError):
    """A required Plaid Sandbox setting is absent."""


def main() -> None:
    """Bootstrap one Sandbox Item for the configured single-user instance."""
    config = _load_config(os.environ)
    token_file = Path(os.environ.get("LEDGE_PLAID_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    if token_file.exists():
        raise PlaidSandboxConfigurationError(
            f"Refusing to overwrite existing token file {token_file}"
        )
    engine = create_database_engine(get_database_url())
    try:
        with httpx2.Client() as client:
            connector = PlaidSandboxConnector(
                session_factory=create_session_factory(engine),
                plaid=PlaidSandboxClient(
                    client=client,
                    client_id=config["client_id"],
                    secret=config["secret"],
                ),
            )
            result = connector.connect(
                user_id=get_configured_user_id(),
                institution_id=config["institution_id"],
            )
    finally:
        engine.dispose()

    _write_connection_secret(
        token_file,
        access_token=result.access_token,
        item_id=result.item_id,
    )
    print(f"Plaid Item connected: {result.item_id}")
    print(f"Ledge sync state: {result.sync_state_id}")
    print(f"Accounts imported: {result.imported_account_count}")
    print(f"Accounts skipped: {result.skipped_account_count}")
    print(f"Sandbox token saved with owner-only permissions: {token_file}")


def _load_config(environ: Mapping[str, str]) -> dict[str, str]:
    values = {
        "client_id": environ.get("PLAID_CLIENT_ID", "").strip(),
        "secret": environ.get("PLAID_SECRET", "").strip(),
        "institution_id": environ.get(
            "PLAID_INSTITUTION_ID", DEFAULT_INSTITUTION_ID
        ).strip(),
    }
    missing = [key for key, value in values.items() if not value]
    if missing:
        variable = {
            "client_id": "PLAID_CLIENT_ID",
            "secret": "PLAID_SECRET",
            "institution_id": "PLAID_INSTITUTION_ID",
        }[missing[0]]
        raise PlaidSandboxConfigurationError(f"{variable} is missing or empty")
    return values


def _write_connection_secret(
    path: Path,
    *,
    access_token: str,
    item_id: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as token_file:
        json.dump(
            {"access_token": access_token, "item_id": item_id},
            token_file,
        )
        token_file.write("\n")


if __name__ == "__main__":
    main()
