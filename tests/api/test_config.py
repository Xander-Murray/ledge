from uuid import UUID

import pytest

from api.config import ApiConfigurationError, get_configured_user_id


def test_configured_user_id_parses_uuid() -> None:
    user_id = get_configured_user_id(
        {"LEDGE_USER_ID": "11111111-1111-1111-1111-111111111111"}
    )

    assert user_id == UUID("11111111-1111-1111-1111-111111111111")


@pytest.mark.parametrize("value", [None, "", "   ", "not-a-uuid"])
def test_configured_user_id_rejects_missing_or_invalid_value(
    value: str | None,
) -> None:
    environ = {} if value is None else {"LEDGE_USER_ID": value}

    with pytest.raises(ApiConfigurationError):
        get_configured_user_id(environ)
