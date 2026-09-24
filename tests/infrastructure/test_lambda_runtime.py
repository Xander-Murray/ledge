from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from infrastructure.lambda_runtime import (
    LambdaRuntimeConfigurationError,
    build_lambda_runtime,
    get_plaid_runtime_secret_id,
)
from infrastructure.secrets_manager import PlaidRuntimeSecret

USER_ID = "11111111-1111-1111-1111-111111111111"
DATABASE_URL = "postgresql+psycopg://ledge:secret@database.example/ledge"
ENVIRONMENT = {
    "LEDGE_USER_ID": USER_ID,
    "LEDGE_DATABASE_URL": DATABASE_URL,
    "LEDGE_PLAID_SECRET_ID": "ledge/dev/plaid",
}
SECRET = PlaidRuntimeSecret(
    client_id="client-id",
    secret="sandbox-secret",
    access_token="access-token",
    item_id="item-id",
)


def test_secret_representation_does_not_expose_credentials() -> None:
    assert "sandbox-secret" not in repr(SECRET)
    assert "access-token" not in repr(SECRET)


def test_http_initialization_failure_disposes_engine() -> None:
    engine = Mock()
    with (
        patch(
            "infrastructure.lambda_runtime.load_plaid_runtime_secret",
            return_value=SECRET,
        ),
        patch(
            "infrastructure.lambda_runtime.create_database_engine", return_value=engine
        ),
        patch(
            "infrastructure.lambda_runtime.httpx2.Client",
            side_effect=RuntimeError("client failure"),
        ),
        pytest.raises(RuntimeError, match="client failure"),
    ):
        build_lambda_runtime(ENVIRONMENT, secrets_client=Mock())
    engine.dispose.assert_called_once_with()


def test_runtime_secret_identity_is_required_and_trimmed() -> None:
    assert (
        get_plaid_runtime_secret_id({"LEDGE_PLAID_SECRET_ID": " ledge/dev/plaid "})
        == "ledge/dev/plaid"
    )

    with pytest.raises(LambdaRuntimeConfigurationError, match="LEDGE_PLAID_SECRET_ID"):
        get_plaid_runtime_secret_id({})


def test_composes_the_worker_from_database_secret_and_plaid_provider() -> None:
    engine = Mock()
    http_client = Mock()
    secrets_client = Mock()
    provider = Mock()
    session_factory = Mock()
    processor = Mock()
    batch_handler = Mock()

    with (
        patch(
            "infrastructure.lambda_runtime.load_plaid_runtime_secret",
            return_value=SECRET,
        ) as load_secret,
        patch(
            "infrastructure.lambda_runtime.create_database_engine",
            return_value=engine,
        ) as create_engine,
        patch(
            "infrastructure.lambda_runtime._build_plaid_provider",
            return_value=provider,
        ) as build_provider,
        patch(
            "infrastructure.lambda_runtime.create_session_factory",
            return_value=session_factory,
        ),
        patch(
            "infrastructure.lambda_runtime.InboundEventProcessor",
            return_value=processor,
        ) as processor_type,
        patch(
            "infrastructure.lambda_runtime.SqsEventBatchHandler",
            return_value=batch_handler,
        ) as batch_handler_type,
    ):
        runtime = build_lambda_runtime(
            ENVIRONMENT,
            secrets_client=secrets_client,
            http_client=http_client,
        )

    assert runtime.engine is engine
    assert runtime.http_client is http_client
    assert runtime.batch_handler is batch_handler
    load_secret.assert_called_once_with(
        secrets_client,
        secret_id="ledge/dev/plaid",
    )
    create_engine.assert_called_once_with(DATABASE_URL)
    build_provider.assert_called_once()
    assert build_provider.call_args.kwargs["runtime_secret"] == SECRET
    processor_type.assert_called_once_with(
        session_factory=session_factory,
        providers={"plaid": provider},
    )
    batch_handler_type.assert_called_once_with(processor=processor)


def test_closes_owned_resources_when_runtime_construction_fails() -> None:
    engine = Mock()
    http_client = Mock()

    with (
        patch(
            "infrastructure.lambda_runtime.load_plaid_runtime_secret",
            return_value=SECRET,
        ),
        patch(
            "infrastructure.lambda_runtime.create_database_engine",
            return_value=engine,
        ),
        patch(
            "infrastructure.lambda_runtime.httpx2.Client",
            return_value=http_client,
        ),
        patch(
            "infrastructure.lambda_runtime._build_plaid_provider",
            side_effect=RuntimeError("injected construction failure"),
        ),
        pytest.raises(RuntimeError, match="injected construction failure"),
    ):
        build_lambda_runtime(ENVIRONMENT, secrets_client=Mock())

    engine.dispose.assert_called_once_with()
    http_client.close.assert_called_once_with()
