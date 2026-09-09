from __future__ import annotations

from unittest.mock import patch
from uuid import UUID

import pytest

from application.event_queue import encode_event_message
from infrastructure.lambda_events import InvalidSqsEventError, SqsEventBatchHandler

FIRST_EVENT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SECOND_EVENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


class RecordingProcessor:
    def __init__(self, *, failing_event_ids: set[UUID] | None = None) -> None:
        self.failing_event_ids = failing_event_ids or set()
        self.processed: list[UUID] = []

    def process(self, event_id: UUID) -> object:
        self.processed.append(event_id)
        if event_id in self.failing_event_ids:
            raise RuntimeError("injected processor failure")
        return object()


def sqs_record(message_id: str, event_id: UUID) -> dict[str, str]:
    return {"messageId": message_id, "body": encode_event_message(event_id)}


def test_processes_every_successful_record() -> None:
    processor = RecordingProcessor()
    handler = SqsEventBatchHandler(processor=processor)

    response = handler.handle(
        {
            "Records": [
                sqs_record("message-1", FIRST_EVENT_ID),
                sqs_record("message-2", SECOND_EVENT_ID),
            ]
        }
    )

    assert processor.processed == [FIRST_EVENT_ID, SECOND_EVENT_ID]
    assert response == {"batchItemFailures": []}


def test_returns_only_failed_records_for_retry() -> None:
    processor = RecordingProcessor(failing_event_ids={FIRST_EVENT_ID})
    handler = SqsEventBatchHandler(processor=processor)

    with patch("infrastructure.lambda_events.logger.exception") as log_exception:
        response = handler.handle(
            {
                "Records": [
                    sqs_record("message-1", FIRST_EVENT_ID),
                    sqs_record("message-2", SECOND_EVENT_ID),
                ]
            }
        )

    assert processor.processed == [FIRST_EVENT_ID, SECOND_EVENT_ID]
    assert response == {"batchItemFailures": [{"itemIdentifier": "message-1"}]}
    log_exception.assert_called_once_with(
        "Failed to process SQS message %s",
        "message-1",
    )


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        '{"schema_version":true,"event_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}',
        '{"schema_version":2,"event_id":"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"}',
        '{"schema_version":1,"event_id":"not-a-uuid"}',
        (
            '{"schema_version":1,"event_id":'
            '"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa","payload":{}}'
        ),
    ],
)
def test_malformed_messages_are_returned_for_retry(body: str) -> None:
    processor = RecordingProcessor()
    handler = SqsEventBatchHandler(processor=processor)

    response = handler.handle(
        {"Records": [{"messageId": "invalid-message", "body": body}]}
    )

    assert processor.processed == []
    assert response == {"batchItemFailures": [{"itemIdentifier": "invalid-message"}]}


@pytest.mark.parametrize(
    "event",
    [
        {},
        {"Records": "not-a-list"},
        {"Records": ["not-an-object"]},
        {"Records": [{"body": encode_event_message(FIRST_EVENT_ID)}]},
    ],
)
def test_rejects_an_invalid_sqs_invocation_envelope(event: dict) -> None:
    handler = SqsEventBatchHandler(processor=RecordingProcessor())

    with pytest.raises(InvalidSqsEventError):
        handler.handle(event)
