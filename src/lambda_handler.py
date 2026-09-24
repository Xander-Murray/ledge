from __future__ import annotations

from collections.abc import Mapping

from infrastructure.lambda_runtime import LambdaRuntime, build_lambda_runtime

_runtime: LambdaRuntime | None = None


def handler(event: Mapping[str, object], context: object) -> dict:
    """AWS Lambda entrypoint for SQS batches of durable Ledge event IDs."""
    global _runtime
    if _runtime is None:
        _runtime = build_lambda_runtime()
    return _runtime.handle(event, context)
