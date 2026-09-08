"""Create and persist a real Plaid Sandbox connection."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from uuid import UUID

import httpx2

from api.config import get_configured_user_id
from application.plaid_connection import PlaidSandboxConnector
from persistence.database import (
    create_database_engine,
    create_session_factory,
    get_database_url,
)
from providers.plaid_config import (
    DEFAULT_TOKEN_FILE,
    PlaidConfigurationError,
    get_plaid_credentials,
)
from providers.plaid_sandbox import PlaidSandboxClient

DEFAULT_INSTITUTION_ID = "ins_109508"


def main(argv: list[str] | None = None) -> None:
    """Bootstrap one Sandbox Item for the configured single-user instance."""
    arguments = _parse_arguments(argv)
    credentials = get_plaid_credentials(os.environ)
    institution_id = os.environ.get(
        "PLAID_INSTITUTION_ID", DEFAULT_INSTITUTION_ID
    ).strip()
    if not institution_id:
        raise PlaidConfigurationError("PLAID_INSTITUTION_ID is missing or empty")
    token_file = arguments.token_file or Path(
        os.environ.get("LEDGE_PLAID_TOKEN_FILE", DEFAULT_TOKEN_FILE)
    )
    if token_file.exists():
        raise PlaidConfigurationError(
            f"Refusing to overwrite existing token file {token_file}"
        )
    engine = create_database_engine(get_database_url())
    try:
        with httpx2.Client() as client:
            connector = PlaidSandboxConnector(
                session_factory=create_session_factory(engine),
                plaid=PlaidSandboxClient(
                    client=client,
                    client_id=credentials.client_id,
                    secret=credentials.secret,
                    username=(
                        "user_transactions_dynamic"
                        if arguments.dynamic_transactions
                        else None
                    ),
                    password=("pass_good" if arguments.dynamic_transactions else None),
                ),
            )
            result = connector.connect(
                user_id=arguments.user_id or get_configured_user_id(),
                institution_id=institution_id,
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


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and persist a real Plaid Sandbox Item."
    )
    parser.add_argument(
        "--dynamic-transactions",
        action="store_true",
        help="use Plaid's realistic, refreshable Transactions test profile",
    )
    parser.add_argument(
        "--user-id",
        type=UUID,
        help="override LEDGE_USER_ID for an isolated Sandbox profile",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        help="override the protected output token file",
    )
    return parser.parse_args(argv)


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
