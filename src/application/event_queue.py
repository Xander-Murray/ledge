from __future__ import annotations

import json
from typing import Protocol
from uuid import UUID

EVENT_MESSAGE_SCHEMA_VERSION = 1


class EventPublishError(RuntimeError):
    """Raised when a durable inbound event cannot be handed to the queue."""


class EventPublisher(Protocol):
    """Publish durable inbound-event identities for asynchronous processing."""

    async def publish(self, event_id: UUID) -> None:
        """Publish one event identity or raise EventPublishError."""
        ...


def encode_event_message(event_id: UUID) -> str:
    """Encode the minimal versioned message shared by the API and worker."""
    return json.dumps(
        {
            "schema_version": EVENT_MESSAGE_SCHEMA_VERSION,
            "event_id": str(event_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    )
