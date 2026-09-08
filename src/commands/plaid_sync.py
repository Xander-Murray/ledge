"""Synchronize real Plaid Sandbox transaction updates into Ledge."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from time import perf_counter, sleep
from uuid import UUID

import httpx2

from api.config import get_configured_user_id
from application.plaid_connection import ledge_account_type, load_plaid_account_ids
from application.synchronization import TransactionSynchronizer
from persistence.database import (
    create_database_engine,
    create_session_factory,
    get_database_url,
)
from providers.plaid import PlaidTransactionProvider
from providers.plaid_config import (
    DEFAULT_TOKEN_FILE,
    get_plaid_credentials,
    load_plaid_connection_secret,
)
from providers.plaid_sandbox import PlaidAccount, PlaidSandboxClient


class PlaidAccountCatalogError(RuntimeError):
    """Plaid's accounts no longer agree with Ledge's stored mappings."""


def main(argv: list[str] | None = None) -> None:
    """Run one or more cursor-based synchronizations for the Sandbox Item."""
    arguments = _parse_arguments(argv)
    _setup_progress(1, "Loading protected Plaid configuration")
    user_id = get_configured_user_id()
    credentials = get_plaid_credentials(os.environ)
    token_file = Path(os.environ.get("LEDGE_PLAID_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    connection = load_plaid_connection_secret(token_file)
    engine = create_database_engine(get_database_url())
    session_factory = create_session_factory(engine)
    try:
        _setup_progress(2, "Loading account mappings from PostgreSQL")
        with session_factory() as session:
            account_ids = load_plaid_account_ids(
                session,
                user_id=user_id,
                item_id=connection.item_id,
            )
        if not account_ids:
            raise PlaidAccountCatalogError(
                "No Plaid account mappings exist for the configured Item"
            )
        print(f"      {len(account_ids)} mapped accounts found", flush=True)

        with httpx2.Client() as client:
            _setup_progress(3, "Checking the current Plaid account catalog")
            plaid = PlaidSandboxClient(
                client=client,
                client_id=credentials.client_id,
                secret=credentials.secret,
            )
            accounts = plaid.get_accounts(access_token=connection.access_token)
            ignored_account_ids = _ignored_account_ids(accounts, account_ids)
            print(
                f"      {len(account_ids)} supported, "
                f"{len(ignored_account_ids)} intentionally ignored",
                flush=True,
            )
            provider = PlaidTransactionProvider(
                client=client,
                client_id=credentials.client_id,
                secret=credentials.secret,
                access_token=connection.access_token,
                account_ids=account_ids,
                ignored_account_ids=ignored_account_ids,
            )
            synchronizer = TransactionSynchronizer(
                session_factory=session_factory,
                provider=provider,
            )
            for cycle in range(1, arguments.iterations + 1):
                if cycle > 1 and arguments.refresh_between:
                    print(
                        "\nRequesting a real Plaid Sandbox transaction refresh...",
                        flush=True,
                    )
                    plaid.refresh_transactions(access_token=connection.access_token)
                    print(
                        f"      waiting {arguments.interval:g}s for Plaid's update...",
                        flush=True,
                    )
                    sleep(arguments.interval)
                _run_cycle(
                    cycle=cycle,
                    cycle_count=arguments.iterations,
                    user_id=user_id,
                    item_id=connection.item_id,
                    synchronizer=synchronizer,
                )
                if cycle < arguments.iterations and not arguments.refresh_between:
                    print(
                        f"      waiting {arguments.interval:g}s for the next cycle...",
                        flush=True,
                    )
                    sleep(arguments.interval)
    finally:
        engine.dispose()

    print("\nPipeline exercise complete.", flush=True)


def _run_cycle(
    *,
    cycle: int,
    cycle_count: int,
    user_id: UUID,
    item_id: str,
    synchronizer: TransactionSynchronizer,
) -> None:
    print(f"\n[cycle {cycle}/{cycle_count}] Synchronizing Plaid updates...", flush=True)
    started_at = perf_counter()
    result = synchronizer.synchronize(
        user_id=user_id,
        provider_name="plaid",
        provider_connection_id=item_id,
    )
    duration_ms = round((perf_counter() - started_at) * 1_000)

    print(
        f"      provider: {result.pages_fetched} page(s), "
        f"+{result.added_count} ~{result.modified_count} -{result.removed_count}",
        flush=True,
    )
    cursor_status = _cursor_status(result.starting_cursor, result.ending_cursor)
    print(f"      cursor:   {cursor_status}", flush=True)
    print(f"      duration: {duration_ms} ms", flush=True)


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continuously exercise the Plaid-to-ledger synchronization pipeline."
        )
    )
    parser.add_argument(
        "--iterations",
        type=_positive_integer,
        default=1,
        help="number of sync cycles to run (default: 1)",
    )
    parser.add_argument(
        "--interval",
        type=_nonnegative_float,
        default=2.0,
        help="seconds between cycles (default: 2)",
    )
    parser.add_argument(
        "--refresh-between",
        action="store_true",
        help="ask Plaid to generate dynamic updates before later cycles",
    )
    return parser.parse_args(argv)


def _positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _nonnegative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _setup_progress(step: int, message: str) -> None:
    print(f"[setup {step}/3] {message}...", flush=True)


def _cursor_status(starting_cursor: str | None, ending_cursor: str) -> str:
    if starting_cursor is None:
        return "initialized and saved"
    if starting_cursor == ending_cursor:
        return "unchanged (already current)"
    return "advanced and saved"


def _ignored_account_ids(
    accounts: tuple[PlaidAccount, ...],
    mapped_account_ids: dict[str, UUID],
) -> frozenset[str]:
    current_ids = {account.provider_account_id for account in accounts}
    missing_ids = set(mapped_account_ids).difference(current_ids)
    if missing_ids:
        raise PlaidAccountCatalogError(
            "A mapped Plaid account is missing from the current Item"
        )
    unmapped_supported_ids = {
        account.provider_account_id
        for account in accounts
        if ledge_account_type(account) is not None
        and account.provider_account_id not in mapped_account_ids
    }
    if unmapped_supported_ids:
        raise PlaidAccountCatalogError(
            "Plaid returned a supported account that has not been mapped"
        )
    return frozenset(
        account.provider_account_id
        for account in accounts
        if ledge_account_type(account) is None
    )


if __name__ == "__main__":
    main()
