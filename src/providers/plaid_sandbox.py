"""Small Plaid Sandbox client used to bootstrap a development Item."""

from __future__ import annotations

from dataclasses import dataclass

import httpx2

from providers.base import ProviderError

PLAID_SANDBOX_URL = "https://sandbox.plaid.com"


class PlaidSandboxResponseError(ProviderError):
    """Plaid returned malformed Sandbox connection data."""


@dataclass(frozen=True, slots=True)
class PlaidAccount:
    provider_account_id: str
    name: str
    account_type: str
    account_subtype: str | None


@dataclass(frozen=True, slots=True)
class PlaidSandboxConnection:
    access_token: str
    item_id: str
    accounts: tuple[PlaidAccount, ...]


class PlaidSandboxClient:
    """Create a Sandbox Item and retrieve the accounts attached to it."""

    def __init__(
        self,
        *,
        client: httpx2.Client,
        client_id: str,
        secret: str,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        if not client_id.strip() or not secret.strip():
            raise ValueError("Plaid credentials must not be empty")
        self._client = client
        self._credentials = {"client_id": client_id, "secret": secret}
        if (username is None) != (password is None):
            raise ValueError("Plaid Sandbox username and password must be paired")
        if username is not None and (not username.strip() or not password.strip()):
            raise ValueError("Plaid Sandbox credentials must not be empty")
        self._user_options = (
            {"override_username": username, "override_password": password}
            if username is not None
            else None
        )

    def create_connection(self, *, institution_id: str) -> PlaidSandboxConnection:
        if not institution_id.strip():
            raise ValueError("Plaid institution ID must not be empty")

        token_request: dict[str, object] = {
            "institution_id": institution_id,
            "initial_products": ["transactions"],
        }
        if self._user_options is not None:
            token_request["options"] = self._user_options
        token_payload = self._post("/sandbox/public_token/create", token_request)
        public_token = _required_text(token_payload, "public_token")

        exchange_payload = self._post(
            "/item/public_token/exchange",
            {"public_token": public_token},
        )
        access_token = _required_text(exchange_payload, "access_token")
        item_id = _required_text(exchange_payload, "item_id")

        accounts = self.get_accounts(access_token=access_token)

        return PlaidSandboxConnection(
            access_token=access_token,
            item_id=item_id,
            accounts=accounts,
        )

    def get_accounts(self, *, access_token: str) -> tuple[PlaidAccount, ...]:
        """Return the current account catalog for a Sandbox Item."""
        if not access_token.strip():
            raise ValueError("Plaid access token must not be empty")
        accounts_payload = self._post("/accounts/get", {"access_token": access_token})
        raw_accounts = accounts_payload.get("accounts")
        if not isinstance(raw_accounts, list):
            raise PlaidSandboxResponseError("Plaid returned invalid account data")

        try:
            accounts = tuple(_parse_account(account) for account in raw_accounts)
        except (TypeError, ValueError):
            raise PlaidSandboxResponseError(
                "Plaid returned invalid account data"
            ) from None
        if len({account.provider_account_id for account in accounts}) != len(accounts):
            raise PlaidSandboxResponseError("Plaid returned duplicate account IDs")

        return accounts

    def refresh_transactions(self, *, access_token: str) -> None:
        """Ask Plaid Sandbox to simulate the Item's next institution refresh."""
        if not access_token.strip():
            raise ValueError("Plaid access token must not be empty")
        self._post("/transactions/refresh", {"access_token": access_token})

    def _post(self, path: str, body: dict[str, object]) -> dict:
        try:
            response = self._client.post(
                f"{PLAID_SANDBOX_URL}{path}",
                json={**self._credentials, **body},
                headers={"Plaid-Version": "2020-09-14"},
                timeout=30.0,
                follow_redirects=False,
            )
        except httpx2.RequestError:
            raise ProviderError("Plaid Sandbox request failed") from None

        try:
            payload = response.json()
        except (ValueError, UnicodeError):
            raise PlaidSandboxResponseError("Plaid returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise PlaidSandboxResponseError("Plaid returned an invalid response object")
        if not response.is_success:
            error_code = payload.get("error_code")
            if _is_safe_error_code(error_code):
                raise ProviderError(
                    f"Plaid rejected the Sandbox request ({error_code})"
                )
            raise ProviderError("Plaid rejected the Sandbox request")
        return payload


def _parse_account(raw_account: object) -> PlaidAccount:
    if not isinstance(raw_account, dict):
        raise TypeError("Account must be an object")
    subtype = raw_account.get("subtype")
    if subtype is not None and not isinstance(subtype, str):
        raise TypeError("Account subtype must be text or null")
    return PlaidAccount(
        provider_account_id=_required_text(raw_account, "account_id"),
        name=_required_text(raw_account, "name"),
        account_type=_required_text(raw_account, "type"),
        account_subtype=subtype,
    )


def _required_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PlaidSandboxResponseError(f"Plaid response is missing {key}")
    return value


def _is_safe_error_code(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= 64
        and all(character.isascii() and (character.isupper() or character == "_")
                for character in value)
    )
