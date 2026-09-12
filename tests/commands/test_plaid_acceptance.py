import httpx2
import pytest

from commands.plaid_acceptance import deliver_duplicate


def test_duplicate_delivery_reuses_exact_notification_body():
    bodies = []
    identity = "11111111-1111-1111-1111-111111111111"

    def respond(request):
        bodies.append(request.content)
        return httpx2.Response(202, json={"id": identity})

    with httpx2.Client(transport=httpx2.MockTransport(respond)) as client:
        assert str(deliver_duplicate(client, "http://localhost", "item")) == identity
    assert len(bodies) == 2 and bodies[0] == bodies[1]


def test_duplicate_delivery_rejects_different_inbox_identities():
    identities = iter(
        [
            "11111111-1111-1111-1111-111111111111",
            "22222222-2222-2222-2222-222222222222",
        ]
    )
    with (
        httpx2.Client(
            transport=httpx2.MockTransport(
                lambda request: httpx2.Response(202, json={"id": next(identities)})
            )
        ) as client,
        pytest.raises(RuntimeError, match="separate inbox"),
    ):
        deliver_duplicate(client, "http://localhost", "item")
