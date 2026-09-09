from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from typing import Protocol
from uuid import UUID

import boto3
from anyio import to_thread
from botocore.exceptions import BotoCoreError, ClientError

from application.event_queue import EventPublishError, encode_event_message


class SqsClient(Protocol):
    """Small portion of the boto3 SQS client used by Ledge."""

    def send_message(self, *, QueueUrl: str, MessageBody: str) -> Mapping[str, object]:
        """Send one message to SQS."""
        ...


class SqsEventPublisher:
    """Publish durable event IDs without exposing financial payloads to SQS."""

    def __init__(self, *, client: SqsClient, queue_url: str) -> None:
        if not queue_url.strip():
            raise ValueError("queue_url must not be empty")
        self._client = client
        self._queue_url = queue_url

    async def publish(self, event_id: UUID) -> None:
        """Send one event ID without blocking FastAPI's event loop."""
        try:
            response = await to_thread.run_sync(
                partial(
                    self._client.send_message,
                    QueueUrl=self._queue_url,
                    MessageBody=encode_event_message(event_id),
                )
            )
        except (BotoCoreError, ClientError) as error:
            raise EventPublishError("SQS rejected the inbound event") from error

        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id.strip():
            raise EventPublishError("SQS returned no message identity")


def create_sqs_event_publisher(
    *,
    queue_url: str,
    region_name: str | None = None,
) -> SqsEventPublisher:
    """Build the production adapter with boto3's normal AWS configuration chain."""
    client = boto3.client("sqs", region_name=region_name)
    return SqsEventPublisher(client=client, queue_url=queue_url)
