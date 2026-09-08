import json
from uuid import UUID

import httpx2
import pytest

from providers.base import ProviderError
from providers.plaid import (
    PlaidPaginationMutationError,
    PlaidResponseError,
    PlaidTransactionProvider,
)

ACCOUNT = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


def transaction(**changes):
    return {
        "account_id": "plaid-account",
        "transaction_id": "posted-1",
        "amount": 12.50,
        "iso_currency_code": "USD",
        "name": "Market",
        "pending": False,
        "pending_transaction_id": "pending-1",
        **changes,
    }


def page(**changes):
    return {
        "added": [],
        "modified": [],
        "removed": [],
        "next_cursor": "next",
        "has_more": False,
        **changes,
    }


def adapter(client):
    return PlaidTransactionProvider(
        client=client,
        client_id="test-client",
        secret="test-secret",
        access_token="test-access-token",
        account_ids={"plaid-account": ACCOUNT},
    )


def test_ignores_only_explicitly_unsupported_accounts():
    with httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(
                200,
                json=page(
                    added=[transaction(account_id="unsupported")],
                    modified=[transaction(account_id="unsupported")],
                    removed=[
                        {"account_id": "unsupported", "transaction_id": "gone"}
                    ],
                ),
            )
        )
    ) as client:
        result = PlaidTransactionProvider(
            client=client,
            client_id="test-client",
            secret="test-secret",
            access_token="test-access-token",
            account_ids={"plaid-account": ACCOUNT},
            ignored_account_ids={"unsupported"},
        ).fetch_transaction_updates(None)

    assert result.added == ()
    assert result.modified == ()
    assert result.removed == ()


def test_rejects_accounts_that_are_neither_mapped_nor_explicitly_ignored():
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200,
                    json=page(added=[transaction(account_id="unknown")]),
                )
            )
        ) as client,
        pytest.raises(PlaidResponseError),
    ):
        adapter(client).fetch_transaction_updates(None)


def test_account_cannot_be_both_mapped_and_ignored():
    with (
        httpx2.Client(transport=httpx2.MockTransport(lambda request: None)) as client,
        pytest.raises(ValueError, match="both mapped and ignored"),
    ):
        PlaidTransactionProvider(
            client=client,
            client_id="test-client",
            secret="test-secret",
            access_token="test-access-token",
            account_ids={"plaid-account": ACCOUNT},
            ignored_account_ids={"plaid-account"},
        )


def test_normalizes_all_changes_and_sends_sandbox_request():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx2.Response(
            200,
            json=page(
                added=[transaction()],
                modified=[
                    transaction(
                        transaction_id="refund",
                        amount=-5.01,
                        pending_transaction_id=None,
                    )
                ],
                removed=[{"account_id": "plaid-account", "transaction_id": "gone"}],
            ),
        )

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        provider = adapter(client)
        result = provider.fetch_transaction_updates(None)
        provider.fetch_transaction_updates("saved")
    assert result.added[0].amount_cents == 1250
    assert result.added[0].account_id == ACCOUNT
    assert result.added[0].pending_provider_transaction_id == "pending-1"
    assert result.modified[0].amount_cents == -501
    assert result.removed[0].provider_transaction_id == "gone"
    assert "cursor" not in json.loads(requests[0].content)
    assert json.loads(requests[1].content)["cursor"] == "saved"
    assert str(requests[0].url) == "https://sandbox.plaid.com/transactions/sync"
    assert requests[0].headers["Plaid-Version"] == "2020-09-14"


@pytest.mark.parametrize(
    "changes",
    [
        {"amount": True},
        {"amount": "12.50"},
        {"amount": 1e30},
        {"iso_currency_code": "EUR"},
        {"account_id": "unknown"},
        {"pending": "false"},
        {"pending_transaction_id": 123},
        {"transaction_id": ""},
    ],
)
def test_rejects_unsafe_transactions(changes):
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    200, json=page(added=[transaction(**changes)])
                )
            )
        ) as client,
        pytest.raises(PlaidResponseError),
    ):
        adapter(client).fetch_transaction_updates(None)


@pytest.mark.parametrize("payload", [[], {}, page(added={}), page(has_more="false")])
def test_rejects_malformed_pages(payload):
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(200, json=payload)
            )
        ) as client,
        pytest.raises(PlaidResponseError),
    ):
        adapter(client).fetch_transaction_updates(None)


def test_pagination_mutation_is_an_explicit_retryable_provider_failure():
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    400,
                    json={
                        "error_code": "TRANSACTIONS_SYNC_MUTATION_DURING_PAGINATION",
                        "error_message": "sensitive provider detail",
                    },
                )
            )
        ) as client,
        pytest.raises(PlaidPaginationMutationError) as error,
    ):
        adapter(client).fetch_transaction_updates("middle-of-batch")
    assert "sensitive" not in str(error.value)


def test_http_failure_does_not_expose_provider_body():
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(
                    401,
                    json={
                        "error_code": "INVALID_API_KEYS",
                        "error_message": "test-secret",
                    },
                )
            )
        ) as client,
        pytest.raises(ProviderError) as error,
    ):
        adapter(client).fetch_transaction_updates(None)
    assert "test-secret" not in str(error.value)
    assert "INVALID_API_KEYS" in str(error.value)


def test_network_failure_is_categorized_without_request_details():
    def respond(request):
        raise httpx2.ReadTimeout("test-secret", request=request)

    with (
        httpx2.Client(transport=httpx2.MockTransport(respond)) as client,
        pytest.raises(ProviderError) as error,
    ):
        adapter(client).fetch_transaction_updates(None)
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize(
    ("raw_amount", "expected_cents"),
    [
        ("1.005", 100),
        ("1.015", 102),
        ("-1.005", -100),
        ("12.50000000000000000000000000000001", 1250),
    ],
)
def test_rounds_provider_amounts_to_cents_without_binary_float_error(
    raw_amount,
    expected_cents,
):
    raw = json.dumps(page(added=[transaction()])).replace("12.5", raw_amount)
    with httpx2.Client(
        transport=httpx2.MockTransport(
            lambda request: httpx2.Response(200, content=raw)
        )
    ) as client:
        result = adapter(client).fetch_transaction_updates(None)

    assert result.added[0].amount_cents == expected_cents
