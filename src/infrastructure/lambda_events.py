from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from application.event_queue import EVENT_MESSAGE_SCHEMA_VERSION

logger = logging.getLogger(__name__)


class EventProcessor(Protocol):
    """Application processor invoked for one durable inbound event."""

    def process(self, event_id: UUID) -> object:
        """Process one event or raise so SQS can retry it."""
        ...


class InvalidSqsEventError(ValueError):
    """Raised when Lambda receives an invalid SQS invocation envelope."""


class InvalidEventMessageError(ValueError):
    """Raised when one SQS record does not contain a supported Ledge message."""


class SqsEventBatchHandler:
    """Process SQS records independently using Lambda partial-batch failures."""

    def __init__(self, *, processor: EventProcessor) -> None:
        self._processor = processor

    def handle(self, event: Mapping[str, object], context: object = None) -> dict:
        """Return only failed message IDs so Lambda acknowledges successes."""
        del context
        records = event.get("Records")
        if not isinstance(records, list):
            raise InvalidSqsEventError("SQS event must contain a Records list")

        failures: list[dict[str, str]] = []
        for record in records:
            message_id = self._message_id(record)
            try:
                event_id = self._event_id(record)
                self._processor.process(event_id)
            except Exception:
                logger.exception("Failed to process SQS message %s", message_id)
                failures.append({"itemIdentifier": message_id})

        return {"batchItemFailures": failures}

    @staticmethod
    def _message_id(record: object) -> str:
        if not isinstance(record, Mapping):
            raise InvalidSqsEventError("SQS record must be an object")
        message_id = record.get("messageId")
        if not isinstance(message_id, str) or not message_id.strip():
            raise InvalidSqsEventError("SQS record must contain a messageId")
        return message_id

    @staticmethod
    def _event_id(record: object) -> UUID:
        if not isinstance(record, Mapping):
            raise InvalidEventMessageError("SQS record must be an object")
        body = record.get("body")
        if not isinstance(body, str):
            raise InvalidEventMessageError("SQS record body must be text")

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise InvalidEventMessageError(
                "SQS record body must be valid JSON"
            ) from None
        if not isinstance(payload, dict):
            raise InvalidEventMessageError("Ledge event message must be an object")
        if set(payload) != {"schema_version", "event_id"}:
            raise InvalidEventMessageError("Ledge event message has invalid fields")
        schema_version = payload["schema_version"]
        if (
            type(schema_version) is not int
            or schema_version != EVENT_MESSAGE_SCHEMA_VERSION
        ):
            raise InvalidEventMessageError("Ledge event message version is unsupported")
        event_id = payload["event_id"]
        if not isinstance(event_id, str):
            raise InvalidEventMessageError("Ledge event ID must be text")
        try:
            return UUID(event_id)
        except ValueError:
            raise InvalidEventMessageError("Ledge event ID must be a UUID") from None
