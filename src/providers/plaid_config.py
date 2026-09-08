"""Validated local configuration for Plaid credentials and Item secrets."""

from __future__ import annotations

import json
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class PlaidConfigurationError(RuntimeError):
    """Plaid runtime credentials or connection secrets are unsafe or invalid."""


@dataclass(frozen=True, slots=True)
class PlaidCredentials:
    client_id: str
    secret: str


@dataclass(frozen=True, slots=True)
class PlaidConnectionSecret:
    access_token: str
    item_id: str


def get_plaid_credentials(environ: Mapping[str, str]) -> PlaidCredentials:
    """Read nonempty Plaid API credentials from an environment mapping."""
    client_id = environ.get("PLAID_CLIENT_ID", "").strip()
    secret = environ.get("PLAID_SECRET", "").strip()
    if not client_id:
        raise PlaidConfigurationError("PLAID_CLIENT_ID is missing or empty")
    if not secret:
        raise PlaidConfigurationError("PLAID_SECRET is missing or empty")
    return PlaidCredentials(client_id=client_id, secret=secret)


def load_plaid_connection_secret(path: Path) -> PlaidConnectionSecret:
    """Load a locally stored Item secret only when its permissions are private."""
    if path.is_symlink():
        raise PlaidConfigurationError(f"Plaid token file must not be a symlink: {path}")
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        raise PlaidConfigurationError(
            f"Plaid token file does not exist: {path}"
        ) from None
    if mode & 0o077:
        raise PlaidConfigurationError(
            f"Plaid token file must have owner-only permissions: {path}"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise PlaidConfigurationError(f"Plaid token file is invalid: {path}") from None
    if not isinstance(payload, dict):
        raise PlaidConfigurationError(f"Plaid token file is invalid: {path}")
    access_token = payload.get("access_token")
    item_id = payload.get("item_id")
    if not isinstance(access_token, str) or not access_token.strip():
        raise PlaidConfigurationError("Plaid token file has no valid access token")
    if not isinstance(item_id, str) or not item_id.strip():
        raise PlaidConfigurationError("Plaid token file has no valid Item ID")
    return PlaidConnectionSecret(access_token=access_token, item_id=item_id)
