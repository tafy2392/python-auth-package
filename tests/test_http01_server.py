import trio
from httpx import ASGITransport, AsyncClient
from testtools.assertions import assert_that  # type: ignore

from marathon_acme_trio.cert_manager import Challenge
from marathon_acme_trio.http01_server import HTTP01Server

from .helpers import all_tasks_idle
from .matchers import is_response_with_body


def app_client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://app")


class TestApp:
    async def test_ping(self):
        """
        Ping? Pong!
        """
        http01 = HTTP01Server.build()
        async with app_client(http01.app) as client:
            resp = await client.get("/ping")
            assert_that(resp, is_response_with_body(b"pong"))

    async def test_challenge_missing(self):
        """
        When the requested challenge is missing, we return a 404.
        """
        http01 = HTTP01Server.build()

        async with app_client(http01.app) as client:
            resp = await client.get("/.well-known/acme-challenge/abc123")
            assert_that(resp, is_response_with_body(b"Not Found", code=404))

    async def test_challenge_existing(self):
        """
        When the requested challenge exists, we return the content.
        """
        http01 = HTTP01Server.build()
        http01.challenges["abc123"] = "accepted"

        async with app_client(http01.app) as client:
            resp = await client.get("/.well-known/acme-challenge/abc123")
            assert_that(resp, is_response_with_body(b"accepted", code=200))

    async def test_receive_challenges_add(self, nursery):
        """
        The recieve_challenges process adds challenges with content to the
        challenge server's internal cache.
        """
        http01 = HTTP01Server.build()
        chal_tx, chal_rx = trio.open_memory_channel(0)

        with trio.fail_after(2):
            nursery.start_soon(http01.receive_challenges, chal_rx)
            await chal_tx.send(Challenge("id1", "content1"))
            await all_tasks_idle()
            assert http01.challenges == {"id1": "content1"}

            await chal_tx.send(Challenge("id2", "content2"))
            await all_tasks_idle()
            assert http01.challenges == {"id1": "content1", "id2": "content2"}

    async def test_receive_challenges_delete(self, nursery):
        """
        The recieve_challenges process removes challenges with no content from
        the challenge server's internal cache.
        """
        http01 = HTTP01Server.build()
        chal_tx, chal_rx = trio.open_memory_channel(0)

        http01.challenges["keep"] = "content"
        http01.challenges["del"] = "obsolete content"

        with trio.fail_after(2):
            nursery.start_soon(http01.receive_challenges, chal_rx)
            await chal_tx.send(Challenge("del", None))
            await all_tasks_idle()
            assert http01.challenges == {"keep": "content"}

    async def test_http01_serves_active_challenges(self, nursery):
        """
        We serve content for active challenges, 404s for unknown or deleted
        challenges.
        """
        http01 = HTTP01Server.build()
        chal_tx, chal_rx = trio.open_memory_channel(0)
        nursery.start_soon(http01.receive_challenges, chal_rx)

        with trio.fail_after(2):
            async with app_client(http01.app) as client:
                # Challenge we've never seen before.
                await assert_challenge_404(client, "abc123")

                # Active challenge.
                await chal_tx.send(Challenge("abc123", "accepted"))
                await assert_challenge_content(client, "abc123", b"accepted")

                # Deleted challenge.
                await chal_tx.send(Challenge("abc123", None))
                await assert_challenge_404(client, "abc123")

    async def test_start(self, nursery):
        """
        HTTP01Server.start starts both the HTTP server and the challenge
        receiver.
        """
        http01 = HTTP01Server.build()
        chal_tx, chal_rx = trio.open_memory_channel(0)

        with trio.fail_after(2):
            await http01.start(nursery, chal_rx)

            async with AsyncClient(base_url="http://localhost:8000") as client:
                # Challenge we've never seen before.
                await assert_challenge_404(client, "abc123")

                # Active challenge.
                await chal_tx.send(Challenge("abc123", "accepted"))
                await assert_challenge_content(client, "abc123", b"accepted")

                # Deleted challenge.
                await chal_tx.send(Challenge("abc123", None))
                await assert_challenge_404(client, "abc123")


async def assert_challenge_content(client, token, content):
    await all_tasks_idle()
    resp = await client.get(f"/.well-known/acme-challenge/{token}")
    assert_that(resp, is_response_with_body(content, code=200))


async def assert_challenge_404(client, token):
    await all_tasks_idle()
    resp = await client.get(f"/.well-known/acme-challenge/{token}")
    assert_that(resp, is_response_with_body(b"Not Found", code=404))
