from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from botocore.exceptions import BotoCoreError, ClientError


class SecretsManagerClient(Protocol):
    """Small portion of the boto3 Secrets Manager client used by Ledge."""

    def get_secret_value(self, *, SecretId: str) -> Mapping[str, object]:
        """Return one secret value from AWS Secrets Manager."""
        ...


class RuntimeSecretError(RuntimeError):
    """Raised when Lambda cannot safely load its Plaid connection secret."""


@dataclass(frozen=True, slots=True)
class PlaidRuntimeSecret:
    client_id: str
    secret: str = field(repr=False)
    access_token: str = field(repr=False)
    item_id: str


def load_plaid_runtime_secret(
    client: SecretsManagerClient,
    *,
    secret_id: str,
) -> PlaidRuntimeSecret:
    """Load and validate the Plaid values needed by the Lambda worker."""
    if not secret_id.strip():
        raise ValueError("secret_id must not be empty")
    try:
        response = client.get_secret_value(SecretId=secret_id)
    except (BotoCoreError, ClientError) as error:
        raise RuntimeSecretError("Unable to load the Lambda runtime secret") from error

    secret_string = response.get("SecretString")
    if not isinstance(secret_string, str):
        raise RuntimeSecretError("Lambda runtime secret must contain JSON text")
    try:
        payload = json.loads(secret_string)
    except json.JSONDecodeError:
        raise RuntimeSecretError(
            "Lambda runtime secret contains invalid JSON"
        ) from None
    if not isinstance(payload, dict):
        raise RuntimeSecretError("Lambda runtime secret must be a JSON object")

    required_fields = {"client_id", "secret", "access_token", "item_id"}
    if set(payload) != required_fields:
        raise RuntimeSecretError("Lambda runtime secret has invalid fields")
    values = {field: payload[field] for field in required_fields}
    if any(
        not isinstance(value, str) or not value.strip() for value in values.values()
    ):
        raise RuntimeSecretError("Lambda runtime secret has an empty value")

    return PlaidRuntimeSecret(
        client_id=values["client_id"].strip(),
        secret=values["secret"].strip(),
        access_token=values["access_token"].strip(),
        item_id=values["item_id"].strip(),
    )
