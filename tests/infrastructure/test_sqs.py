from __future__ import annotations

from collections.abc import Mapping
from unittest.mock import patch
from uuid import UUID

import pytest
from anyio import run
from botocore.exceptions import EndpointConnectionError

from application.event_queue import EventPublishError
from infrastructure.sqs import SqsEventPublisher, create_sqs_event_publisher

EVENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")


class RecordingSqsClient:
    def __init__(self, response: Mapping[str, object] | None = None) -> None:
        self.response = response or {"MessageId": "sqs-message-1"}
        self.calls: list[dict[str, str]] = []

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> Mapping[str, object]:
        self.calls.append({"QueueUrl": QueueUrl, "MessageBody": MessageBody})
        return self.response


class UnavailableSqsClient:
    def send_message(self, *, QueueUrl: str, MessageBody: str) -> Mapping[str, object]:
        raise EndpointConnectionError(endpoint_url=QueueUrl)


@pytest.fixture(autouse=True)
def execute_thread_calls_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests focused on the adapter rather than AnyIO's thread pool."""

    async def run_sync(function):
        return function()

    monkeypatch.setattr("infrastructure.sqs.to_thread.run_sync", run_sync)


def test_publishes_minimal_versioned_event_message() -> None:
    client = RecordingSqsClient()
    publisher = SqsEventPublisher(
        client=client,
        queue_url="https://sqs.us-east-1.amazonaws.com/123/ledge-events",
    )

    run(publisher.publish, EVENT_ID)

    assert client.calls == [
        {
            "QueueUrl": "https://sqs.us-east-1.amazonaws.com/123/ledge-events",
            "MessageBody": (
                '{"event_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","schema_version":1}'
            ),
        }
    ]


def test_wraps_aws_delivery_failures() -> None:
    publisher = SqsEventPublisher(
        client=UnavailableSqsClient(),
        queue_url="https://sqs.us-east-1.amazonaws.com/123/ledge-events",
    )

    with pytest.raises(EventPublishError, match="SQS rejected"):
        run(publisher.publish, EVENT_ID)


def test_rejects_an_sqs_response_without_a_message_id() -> None:
    publisher = SqsEventPublisher(
        client=RecordingSqsClient(response={"MD5OfMessageBody": "digest"}),
        queue_url="https://sqs.us-east-1.amazonaws.com/123/ledge-events",
    )

    with pytest.raises(EventPublishError, match="no message identity"):
        run(publisher.publish, EVENT_ID)


@pytest.mark.parametrize("queue_url", ["", "   "])
def test_rejects_an_empty_queue_url(queue_url: str) -> None:
    with pytest.raises(ValueError, match="queue_url"):
        SqsEventPublisher(client=RecordingSqsClient(), queue_url=queue_url)


def test_factory_uses_boto3s_standard_configuration_chain() -> None:
    client = RecordingSqsClient()

    with patch(
        "infrastructure.sqs.boto3.client", return_value=client
    ) as client_factory:
        publisher = create_sqs_event_publisher(
            queue_url="https://sqs.us-west-2.amazonaws.com/123/ledge-events",
            region_name="us-west-2",
        )

    assert isinstance(publisher, SqsEventPublisher)
    client_factory.assert_called_once_with("sqs", region_name="us-west-2")
