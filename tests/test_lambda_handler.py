from __future__ import annotations

from unittest.mock import Mock, patch

import lambda_handler


def test_reuses_one_runtime_across_warm_lambda_invocations(
    monkeypatch,
) -> None:
    runtime = Mock()
    runtime.handle.side_effect = [
        {"batchItemFailures": []},
        {"batchItemFailures": [{"itemIdentifier": "message-2"}]},
    ]
    monkeypatch.setattr(lambda_handler, "_runtime", None)

    first_event = {"Records": []}
    second_event = {"Records": [{"messageId": "message-2"}]}
    context = object()
    with patch("lambda_handler.build_lambda_runtime", return_value=runtime) as build:
        first_response = lambda_handler.handler(first_event, context)
        second_response = lambda_handler.handler(second_event, context)

    assert first_response == {"batchItemFailures": []}
    assert second_response == {"batchItemFailures": [{"itemIdentifier": "message-2"}]}
    build.assert_called_once_with()
    assert runtime.handle.call_args_list[0].args == (first_event, context)
    assert runtime.handle.call_args_list[1].args == (second_event, context)
