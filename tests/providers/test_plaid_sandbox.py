import json

import httpx2
import pytest

from providers.base import ProviderError
from providers.plaid_sandbox import (
    PlaidSandboxClient,
    PlaidSandboxResponseError,
)


def test_creates_exchanges_and_loads_a_sandbox_item() -> None:
    requests = []
    responses = iter(
        [
            {"public_token": "public-sandbox-token"},
            {"access_token": "access-sandbox-token", "item_id": "item-1"},
            {
                "accounts": [
                    {
                        "account_id": "plaid-checking",
                        "name": "Plaid Checking",
                        "type": "depository",
                        "subtype": "checking",
                    }
                ]
            },
        ]
    )

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=next(responses))

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        connection = PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
        ).create_connection(institution_id="ins_109508")

    assert connection.item_id == "item-1"
    assert connection.access_token == "access-sandbox-token"
    assert connection.accounts[0].provider_account_id == "plaid-checking"
    assert connection.accounts[0].account_subtype == "checking"
    assert [request.url.path for request in requests] == [
        "/sandbox/public_token/create",
        "/item/public_token/exchange",
        "/accounts/get",
    ]
    assert json.loads(requests[0].content) == {
        "client_id": "client-id",
        "secret": "sandbox-secret",
        "institution_id": "ins_109508",
        "initial_products": ["transactions"],
    }
    assert json.loads(requests[1].content)["public_token"] == "public-sandbox-token"
    assert json.loads(requests[2].content)["access_token"] == "access-sandbox-token"
    assert all(request.headers["Plaid-Version"] == "2020-09-14" for request in requests)


def test_dynamic_transaction_profile_is_sent_during_item_creation() -> None:
    requests = []
    responses = iter(
        [
            {"public_token": "public-token"},
            {"access_token": "access-token", "item_id": "item-1"},
            {"accounts": []},
        ]
    )

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json=next(responses))

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
            username="user_transactions_dynamic",
            password="pass_good",
        ).create_connection(institution_id="ins_109508")

    assert json.loads(requests[0].content)["options"] == {
        "override_username": "user_transactions_dynamic",
        "override_password": "pass_good",
    }


def test_requests_a_real_plaid_transaction_refresh() -> None:
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(200, json={"request_id": "request-1"})

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
        ).refresh_transactions(access_token="access-token")

    assert requests[0].url.path == "/transactions/refresh"
    assert json.loads(requests[0].content)["access_token"] == "access-token"


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(200, content=b"not-json"),
        httpx2.Response(200, json=[]),
        httpx2.Response(200, json={}),
    ],
)
def test_rejects_malformed_public_token_responses(response) -> None:
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(lambda request: response)
        ) as client,
        pytest.raises(PlaidSandboxResponseError),
    ):
        PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
        ).create_connection(institution_id="ins_109508")


def test_provider_failure_does_not_expose_credentials_or_response() -> None:
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    401,
                    json={
                        "error_code": "INVALID_API_KEYS",
                        "error_message": "sandbox-secret",
                    },
                )
            )
        ) as client,
        pytest.raises(ProviderError) as error,
    ):
        PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
        ).create_connection(institution_id="ins_109508")
    assert "sandbox-secret" not in str(error.value)
    assert "INVALID_API_KEYS" in str(error.value)


def test_rejects_duplicate_provider_account_ids() -> None:
    responses = iter(
        [
            {"public_token": "public-token"},
            {"access_token": "access-token", "item_id": "item-1"},
            {
                "accounts": [
                    {
                        "account_id": "duplicate",
                        "name": name,
                        "type": "depository",
                        "subtype": "checking",
                    }
                    for name in ("First", "Second")
                ]
            },
        ]
    )
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=next(responses))
            )
        ) as client,
        pytest.raises(PlaidSandboxResponseError, match="duplicate"),
    ):
        PlaidSandboxClient(
            client=client,
            client_id="client-id",
            secret="sandbox-secret",
        ).create_connection(institution_id="ins_109508")
