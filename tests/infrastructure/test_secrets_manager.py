from __future__ import annotations

import json
from collections.abc import Mapping

import pytest
from botocore.exceptions import EndpointConnectionError

from infrastructure.secrets_manager import (
    PlaidRuntimeSecret,
    RuntimeSecretError,
    load_plaid_runtime_secret,
)


class RecordingSecretsClient:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.secret_ids: list[str] = []

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, object]:
        self.secret_ids.append(SecretId)
        return self.response


class UnavailableSecretsClient:
    def get_secret_value(self, *, SecretId: str) -> Mapping[str, object]:
        raise EndpointConnectionError(endpoint_url="https://secretsmanager.invalid")


def test_loads_and_trims_the_complete_plaid_runtime_secret() -> None:
    client = RecordingSecretsClient(
        {
            "SecretString": json.dumps(
                {
                    "client_id": " client-id ",
                    "secret": " sandbox-secret ",
                    "access_token": " access-token ",
                    "item_id": " item-id ",
                }
            )
        }
    )

    secret = load_plaid_runtime_secret(client, secret_id="ledge/dev/plaid")

    assert secret == PlaidRuntimeSecret(
        client_id="client-id",
        secret="sandbox-secret",
        access_token="access-token",
        item_id="item-id",
    )
    assert client.secret_ids == ["ledge/dev/plaid"]


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"SecretBinary": b"not-supported"},
        {"SecretString": "not-json"},
        {"SecretString": "[]"},
        {"SecretString": "{}"},
        {
            "SecretString": json.dumps(
                {
                    "client_id": "client-id",
                    "secret": "secret",
                    "access_token": "access-token",
                    "item_id": "item-id",
                    "unexpected": "value",
                }
            )
        },
        {
            "SecretString": json.dumps(
                {
                    "client_id": "client-id",
                    "secret": " ",
                    "access_token": "access-token",
                    "item_id": "item-id",
                }
            )
        },
    ],
)
def test_rejects_incomplete_or_malformed_runtime_secrets(
    response: Mapping[str, object],
) -> None:
    with pytest.raises(RuntimeSecretError):
        load_plaid_runtime_secret(
            RecordingSecretsClient(response),
            secret_id="ledge/dev/plaid",
        )


def test_wraps_aws_failures_without_exposing_the_secret_identity() -> None:
    secret_id = "private-secret-name"

    with pytest.raises(RuntimeSecretError) as error:
        load_plaid_runtime_secret(
            UnavailableSecretsClient(),
            secret_id=secret_id,
        )

    assert secret_id not in str(error.value)


def test_rejects_an_empty_secret_identity_before_calling_aws() -> None:
    client = RecordingSecretsClient({})

    with pytest.raises(ValueError, match="secret_id"):
        load_plaid_runtime_secret(client, secret_id="   ")

    assert client.secret_ids == []
