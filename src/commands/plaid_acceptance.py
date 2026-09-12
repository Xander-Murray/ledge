"""Exercise normalized HTTP intake and the local worker with real Sandbox data."""

import argparse
import os
from pathlib import Path
from time import sleep
from uuid import UUID, uuid4

import httpx2

from api.config import get_configured_user_id
from application.event_processing import InboundEventProcessor
from application.plaid_connection import (
    PlaidAccountCatalogError,
    ignored_plaid_account_ids,
    load_plaid_account_ids,
)
from commands.evidence import ledger_snapshot, print_changes
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
from providers.plaid_sandbox import PlaidSandboxClient


def deliver_duplicate(client, api_url: str, item_id: str) -> UUID:
    body = {
        "provider_name": "plaid",
        "provider_connection_id": item_id,
        "provider_event_id": f"acceptance-{uuid4()}",
        "event_type": "transactions.updated",
        "payload": {"source": "manual-sandbox-acceptance"},
    }
    identities = []
    for _ in range(2):
        response = client.post(
            f"{api_url.rstrip('/')}/webhooks/transactions",
            json=body,
            timeout=30,
            follow_redirects=False,
        )
        response.raise_for_status()
        if response.status_code != 202:
            raise RuntimeError("Intake did not return 202 Accepted")
        identities.append(UUID(response.json()["id"]))
    if identities[0] != identities[1]:
        raise RuntimeError("Duplicate notification created separate inbox identities")
    return identities[0]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--interval", type=int, default=10)
    args = parser.parse_args(argv)
    if not 0 <= args.interval <= 60:
        parser.error("--interval must be between 0 and 60 seconds")
    user_id = get_configured_user_id()
    credentials = get_plaid_credentials(os.environ)
    connection = load_plaid_connection_secret(
        Path(os.environ.get("LEDGE_PLAID_TOKEN_FILE", DEFAULT_TOKEN_FILE))
    )
    engine = create_database_engine(get_database_url())
    sessions = create_session_factory(engine)
    try:
        with httpx2.Client() as client:
            with sessions() as session:
                mappings = load_plaid_account_ids(
                    session,
                    user_id=user_id,
                    item_id=connection.item_id,
                )
            if not mappings:
                raise PlaidAccountCatalogError(
                    "Configured Item has no account mappings"
                )
            plaid = PlaidSandboxClient(
                client=client,
                client_id=credentials.client_id,
                secret=credentials.secret,
            )
            provider = PlaidTransactionProvider(
                client=client,
                client_id=credentials.client_id,
                secret=credentials.secret,
                access_token=connection.access_token,
                account_ids=mappings,
                ignored_account_ids=ignored_plaid_account_ids(
                    plaid.get_accounts(access_token=connection.access_token),
                    mappings,
                ),
            )
            worker = InboundEventProcessor(
                session_factory=sessions,
                providers={"plaid": provider},
            )

            def snapshot():
                with sessions() as session:
                    return ledger_snapshot(session, user_id, set(mappings.values()))

            print(
                "Real Plaid Sandbox data; manually generated normalized notifications."
            )
            print("Local worker execution; this does not test SQS/Lambda delivery.")
            initial = snapshot()
            for cycle in range(2):
                if cycle:
                    plaid.refresh_transactions(access_token=connection.access_token)
                    sleep(args.interval)
                before = snapshot()
                identity = deliver_duplicate(client, args.api_url, connection.item_id)
                result = worker.process(identity)
                after = snapshot()
                duplicate = worker.process(identity)
                if not duplicate.already_processed or snapshot() != after:
                    raise RuntimeError(
                        "Repeated worker delivery changed financial state"
                    )
                print_changes(before, after)
                print(
                    f"PASS: duplicate HTTP intake and worker replay; event {identity}"
                )
                print(f"Worker duration: {result.processing_duration_ms:.3f} ms")
                if cycle == 0:
                    initial = after
            final = snapshot()
            replacements = [
                key
                for key, value in final["transactions"].items()
                if value["replaces"] in initial["transactions"]
                and initial["transactions"][value["replaces"]]["status"] == "active"
                and final["transactions"][value["replaces"]]["status"] == "replaced"
            ]
            if not replacements:
                raise RuntimeError(
                    "No pending-to-posted transition observed. Evidence incomplete; "
                    "verify a dynamic Item and allow more refresh time."
                )
            print(f"PASS: observed {len(replacements)} pending-to-posted replacements")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
