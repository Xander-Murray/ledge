from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import boto3
import httpx2
from sqlalchemy.engine import Engine

from api.config import get_configured_user_id
from application.event_processing import InboundEventProcessor
from application.plaid_connection import (
    PlaidAccountCatalogError,
    ignored_plaid_account_ids,
    load_plaid_account_ids,
)
from infrastructure.lambda_events import SqsEventBatchHandler
from infrastructure.secrets_manager import (
    PlaidRuntimeSecret,
    SecretsManagerClient,
    load_plaid_runtime_secret,
)
from persistence.database import (
    create_database_engine,
    create_session_factory,
    get_database_url,
)
from providers.plaid import PlaidTransactionProvider
from providers.plaid_sandbox import PlaidSandboxClient

PLAID_RUNTIME_SECRET_ID_ENV_VAR = "LEDGE_PLAID_SECRET_ID"


class LambdaRuntimeConfigurationError(RuntimeError):
    """Raised when the deployed worker is missing required configuration."""


@dataclass(slots=True)
class LambdaRuntime:
    """Long-lived clients and handler reused by warm Lambda invocations."""

    engine: Engine
    http_client: httpx2.Client
    batch_handler: SqsEventBatchHandler

    def handle(self, event: Mapping[str, object], context: object = None) -> dict:
        return self.batch_handler.handle(event, context)


def get_plaid_runtime_secret_id(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Return the Secrets Manager identity configured for the Lambda worker."""
    source = os.environ if environ is None else environ
    secret_id = source.get(PLAID_RUNTIME_SECRET_ID_ENV_VAR, "").strip()
    if not secret_id:
        raise LambdaRuntimeConfigurationError(
            f"{PLAID_RUNTIME_SECRET_ID_ENV_VAR} is missing or empty"
        )
    return secret_id


def build_lambda_runtime(
    environ: Mapping[str, str] | None = None,
    *,
    secrets_client: SecretsManagerClient | None = None,
    http_client: httpx2.Client | None = None,
) -> LambdaRuntime:
    """Compose the production SQS worker from environment and AWS configuration."""
    source = os.environ if environ is None else environ
    user_id = get_configured_user_id(source)
    database_url = get_database_url(source)
    secret_id = get_plaid_runtime_secret_id(source)
    if secrets_client is None:
        secrets_client = boto3.client("secretsmanager")
    runtime_secret = load_plaid_runtime_secret(
        secrets_client,
        secret_id=secret_id,
    )

    engine = create_database_engine(database_url)
    owns_http_client = http_client is None
    client = http_client
    try:
        if client is None:
            client = httpx2.Client()
        provider = _build_plaid_provider(
            engine=engine,
            http_client=client,
            user_id=user_id,
            runtime_secret=runtime_secret,
        )
        processor = InboundEventProcessor(
            session_factory=create_session_factory(engine),
            providers={"plaid": provider},
        )
        return LambdaRuntime(
            engine=engine,
            http_client=client,
            batch_handler=SqsEventBatchHandler(processor=processor),
        )
    except Exception:
        engine.dispose()
        if owns_http_client and client is not None:
            client.close()
        raise


def _build_plaid_provider(
    *,
    engine: Engine,
    http_client: httpx2.Client,
    user_id: UUID,
    runtime_secret: PlaidRuntimeSecret,
) -> PlaidTransactionProvider:
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        account_ids = load_plaid_account_ids(
            session,
            user_id=user_id,
            item_id=runtime_secret.item_id,
        )
    if not account_ids:
        raise PlaidAccountCatalogError(
            "No Plaid account mappings exist for the configured Item"
        )

    plaid = PlaidSandboxClient(
        client=http_client,
        client_id=runtime_secret.client_id,
        secret=runtime_secret.secret,
    )
    accounts = plaid.get_accounts(access_token=runtime_secret.access_token)
    ignored_account_ids = ignored_plaid_account_ids(accounts, account_ids)
    return PlaidTransactionProvider(
        client=http_client,
        client_id=runtime_secret.client_id,
        secret=runtime_secret.secret,
        access_token=runtime_secret.access_token,
        account_ids=account_ids,
        ignored_account_ids=ignored_account_ids,
    )
