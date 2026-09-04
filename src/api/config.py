from __future__ import annotations

import os
from collections.abc import Mapping
from uuid import UUID

USER_ID_ENV_VAR = "LEDGE_USER_ID"


class ApiConfigurationError(RuntimeError):
    """Raised when the API's runtime identity is missing or invalid."""


def get_configured_user_id(environ: Mapping[str, str] | None = None) -> UUID:
    """Return the single-user identity configured for this Ledge instance."""
    source = os.environ if environ is None else environ
    raw_user_id = source.get(USER_ID_ENV_VAR)

    if raw_user_id is None or not raw_user_id.strip():
        raise ApiConfigurationError(f"{USER_ID_ENV_VAR} is missing or empty")

    try:
        return UUID(raw_user_id.strip())
    except ValueError as error:
        raise ApiConfigurationError(
            f"{USER_ID_ENV_VAR} must be a valid UUID"
        ) from error
