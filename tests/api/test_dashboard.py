from uuid import UUID

import pytest


@pytest.mark.integration
@pytest.mark.anyio
async def test_dashboard_is_scoped_and_history_is_private(api_client_factory):
    async for client in api_client_factory(
        UUID("11111111-1111-1111-1111-111111111111")
    ):
        response = await client.get("/")
        assert response.status_code == 200
        assert "Current activity" in response.text
        assert "account balances" in response.text
        assert "10000000-0000-0000-0000-000000000004" not in response.text
        own = await client.get("/activity/10000000-0000-0000-0000-000000000001")
        assert own.status_code == 200
        other = await client.get("/activity/10000000-0000-0000-0000-000000000004")
        assert other.status_code == 404
        invalid = await client.get("/?offset=-1")
        assert invalid.status_code == 422


@pytest.mark.integration
@pytest.mark.anyio
async def test_history_filter_and_provider_text_are_safe(
    api_database, api_client_factory
):
    from sqlalchemy import create_engine, update
    from sqlalchemy.orm import Session

    from persistence.models import ExternalTransactionModel

    identity = UUID("10000000-0000-0000-0000-000000000001")
    unsafe = '<script>alert("provider")</script>'
    engine = create_engine(api_database)
    try:
        with Session(engine) as session, session.begin():
            session.execute(
                update(ExternalTransactionModel)
                .where(
                    ExternalTransactionModel.id == identity,
                )
                .values(description=unsafe)
            )
    finally:
        engine.dispose()
    async for client in api_client_factory(
        UUID("11111111-1111-1111-1111-111111111111")
    ):
        current = await client.get("/")
        history = await client.get("/?include_history=true")
        detail = await client.get(f"/activity/{identity}")
        assert "10000000-0000-0000-0000-000000000003" not in current.text
        assert "10000000-0000-0000-0000-000000000003" in history.text
        assert "Recorded activity" in history.text
        for response in (current, history, detail):
            assert response.status_code == 200
            assert unsafe not in response.text
            assert "&lt;script&gt;" in response.text


def test_exact_money_and_viewport_metadata():
    from api.dashboard import money, page

    assert money(-105) == "-$1.05"
    assert money(9007199254740993) == "$90,071,992,547,409.93"
    assert b"width=device-width" in page("").body
