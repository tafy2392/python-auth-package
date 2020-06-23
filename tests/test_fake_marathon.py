import json

import pytest  # type: ignore
import trio
from testtools.assertions import assert_that  # type: ignore
from testtools.matchers import Equals, Is, MatchesListwise  # type: ignore

from .fake_marathon import FakeMarathon, FakeMarathonLb
from .helpers import mkapp
from .matchers import is_marathon_event, is_response, is_response_with_body


@pytest.fixture
async def fake_marathon():
    async with FakeMarathon() as fake_marathon:
        yield fake_marathon


@pytest.fixture
async def fake_old_marathon():
    async with FakeMarathon(filter_events=False) as fake_marathon:
        yield fake_marathon


@pytest.fixture
async def fake_mlb(nursery):
    async with FakeMarathonLb() as fake_mlb:
        await fake_mlb.start_http(nursery)
        yield fake_mlb


def is_plain_response(body):
    return is_response_with_body(body, "text/plain")


def is_json_response(body):
    return is_response_with_body(body, "application/json", method="json")


def _ev_with_ra(event_type, remote_address):
    return is_marathon_event(event_type, remoteAddress=Equals(remote_address))


def ev_attached(remote_address):
    return _ev_with_ra("event_stream_attached", remote_address)


def ev_detached(remote_address):
    return _ev_with_ra("event_stream_detached", remote_address)


def ev_app_post(uri, app_dict):
    return is_marathon_event(
        "api_post_event",
        clientIp=Is(None),
        uri=Equals(uri),
        appDefinition=Equals(app_dict),
    )


def _get_stream(client, path, params):
    headers = {"Accept": "text/event-stream"}
    return client.stream("GET", path, headers=headers, params=params)


class EventStream:
    def __init__(self, nursery, client, event_types=None):
        self._nursery = nursery
        self.data = b""
        self._cancel_scope = trio.CancelScope()
        nursery.start_soon(self._fetch_events, client, event_types)

    async def _fetch_events(self, client, event_types):
        params = {}
        if event_types:
            params["event_type"] = event_types
        try:
            async with _get_stream(client, "/v2/events", params) as resp:
                assert_that(resp, is_response(200, "text/event-stream"))
                self._resp = resp
                async for data in self._resp.aiter_bytes():
                    self.data += data
        except trio.ClosedResourceError:
            # We disconnect by closing the response object from outside the
            # context manager, so we get an exception here that we have to
            # swallow.
            pass

    async def close(self):
        await self._resp.aclose()

    def parse_events(self):
        lines = self.data.splitlines()
        events = []
        while len(lines) > 2:
            [etype, edata, nl, *lines] = lines
            assert etype[:7] == b"event: "
            assert edata[:6] == b"data: "
            event = json.loads(edata[6:])
            assert event["eventType"] == etype[7:].decode("utf-8")
            events.append(event)
        return events

    def assert_events(self, *matchers):
        assert_that(self.parse_events(), MatchesListwise(matchers))


async def all_tasks_idle(cushion=0.0001):
    await trio.testing.wait_all_tasks_blocked(cushion)


class TestFakeMarathon:
    async def test_get_apps_empty(self):
        """
        When there are no apps to get, an empty app list is returned.
        """
        async with FakeMarathon() as fake_marathon:
            resp = await fake_marathon.get_client().get("/v2/apps")
            assert_that(resp, is_json_response({"apps": []}))

    async def test_get_apps(self):
        """
        When the list of apps is requested, a list of apps added via add_app()
        should be returned.
        """
        app = mkapp("/my-app_1")

        async with FakeMarathon() as fake_marathon:
            await fake_marathon.add_app(app)
            resp = await fake_marathon.get_client().get("/v2/apps")
            assert_that(resp, is_json_response({"apps": [app]}))

    async def test_get_apps_check_called(self):
        """
        When a client makes a call to the GET /v2/apps API, a flag should be
        set to indicate that the API has been called. Checking the flag should
        reset it.
        """
        async with FakeMarathon() as fake_marathon:
            # The flag should start out False
            assert_that(fake_marathon.check_called_get_apps(), Equals(False))

            # Make a call to get_apps()
            resp = await fake_marathon.get_client().get("/v2/apps")
            assert_that(resp, is_json_response({"apps": []}))

            # After the call the flag should be True
            assert_that(fake_marathon.check_called_get_apps(), Equals(True))

            # Checking the flag should reset it to False
            assert_that(fake_marathon.check_called_get_apps(), Equals(False))

    async def test_get_events(self, nursery, fake_marathon):
        """
        When a request is made to the event stream endpoint, an SSE stream
        should be received in response and an event should be fired that
        indicates that the stream was attached to.
        """
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_marathon.start_http(nursery)
        es = EventStream(nursery, fake_marathon.get_http_client())
        # Wait for the first event to arrive.
        await all_tasks_idle()
        es.assert_events(ev_attached("127.0.0.1"))

    @pytest.mark.xfail(reason="gitlab.com/pgjones/hypercorn/-/issues/124")
    async def test_get_events_lost_connection(self, nursery, fake_marathon):
        """
        When two connections are made to the event stream, the first connection
        should receive events for both connections attaching to the stream.
        Then, when the first connection is disconnected, the second should
        receive a detach event for the first.
        """
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_marathon.start_http(nursery)
        # Connect for the first time.
        es1 = EventStream(nursery, fake_marathon.get_http_client())
        await all_tasks_idle()
        es1.assert_events(ev_attached("127.0.0.1"))

        # Connect for the second time.
        es2 = EventStream(nursery, fake_marathon.get_http_client())
        await all_tasks_idle()
        es2.assert_events(ev_attached("127.0.0.1"))
        # We've now seen two attach events on the first stream.
        es1.assert_events(ev_attached("127.0.0.1"), ev_attached("127.0.0.1"))

        # Close first connection.
        await es1.close()
        await all_tasks_idle()
        es2.assert_events(ev_attached("127.0.0.1"), ev_detached("127.0.0.1"))
        # First connection doesn't see its own detach event.
        es1.assert_events(ev_attached("127.0.0.1"), ev_attached("127.0.0.1"))

    async def test_add_app_triggers_event(self, nursery, fake_marathon):
        """
        When an app is added to the underlying fake Marathon, an
        ``api_post_event`` should be received by any event listeners.
        """
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_marathon.start_http(nursery)
        es = EventStream(nursery, fake_marathon.get_http_client())
        await all_tasks_idle()

        await fake_marathon.add_app(app)
        await all_tasks_idle()

        es.assert_events(
            ev_attached("127.0.0.1"), ev_app_post("/v2/apps/my-app_1", app),
        )

    async def test_add_apps_triggers_events(self, nursery, fake_marathon):
        """
        When an app is added to the underlying fake Marathon, an
        ``api_post_event`` should be received by any event listeners.
        """
        app1 = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        app2 = mkapp("/my-app_2", MARATHON_ACME_0_DOMAIN="example2.com")
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_marathon.start_http(nursery)
        es = EventStream(nursery, fake_marathon.get_http_client())
        await all_tasks_idle()

        await fake_marathon.add_apps(app1, app2)
        await all_tasks_idle()

        es.assert_events(
            ev_attached("127.0.0.1"),
            ev_app_post("/v2/apps/my-app_1", app1),
            ev_app_post("/v2/apps/my-app_2", app2),
        )

    async def test_get_events_event_types(self, nursery, fake_marathon):
        """
        When a request is made to the event stream endpoint, and a set of
        event_types are specified, an SSE stream should be received in response
        and only the event types that were specified should be fired.
        """
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_marathon.start_http(nursery)
        client = fake_marathon.get_http_client()
        es = EventStream(nursery, client, event_types=["api_post_event"])
        await all_tasks_idle()

        await fake_marathon.add_app(app)
        await all_tasks_idle()

        es.assert_events(ev_app_post("/v2/apps/my-app_1", app))

    async def test_get_events_no_filter(self, nursery, fake_old_marathon):
        """
        The event stream endpoint can ignore event_types to emulate older
        marathons that don't support filtering.
        """
        app = mkapp("/my-app_1", MARATHON_ACME_0_DOMAIN="example.com")
        # FIXME: Streaming doesn't seem to work properly without a network
        # between the client and server, so for now we have to use an actual
        # HTTP server.
        await fake_old_marathon.start_http(nursery)
        client = fake_old_marathon.get_http_client()
        es = EventStream(nursery, client, event_types=["api_post_event"])
        await all_tasks_idle()

        await fake_old_marathon.add_app(app)
        await all_tasks_idle()

        es.assert_events(
            ev_attached("127.0.0.1"), ev_app_post("/v2/apps/my-app_1", app),
        )

    async def test_add_app_api(self):
        """
        We can add an app with an API call.
        """
        app = mkapp("/my-app_1")

        async with FakeMarathon() as fake_marathon:
            client = fake_marathon.get_client()
            resp = await client.post("/v2/apps", json=app)
            assert_that(resp, is_json_response(app))
            resp = await client.get("/v2/apps")
            assert_that(resp, is_json_response({"apps": [app]}))


class TestFakeMarathonLb(object):
    async def test_signal_hup(self, fake_mlb):
        """
        When a client calls the ``/mlb_signal/hup`` endpoint, the correct
        response should be returned and the ``signalled_hup`` flag set True.
        """
        assert_that(fake_mlb.check_signalled_hup(), Is(False))

        resp = await fake_mlb.get_http_client().post("/_mlb_signal/hup")
        assert_that(
            resp, is_plain_response(b"Sent SIGHUP signal to marathon-lb")
        )

        assert_that(fake_mlb.check_signalled_hup(), Is(True))

        # Signalled flag should be reset to false after it is checked
        assert_that(fake_mlb.check_signalled_hup(), Is(False))

    async def test_signal_usr1(self, fake_mlb):
        """
        When a client calls the ``/mlb_signal/usr1`` endpoint, the correct
        response should be returned and the ``signalled_usr1`` flag set True.
        """
        assert_that(fake_mlb.check_signalled_usr1(), Is(False))

        resp = await fake_mlb.get_http_client().post("/_mlb_signal/usr1")
        assert_that(
            resp, is_plain_response(b"Sent SIGUSR1 signal to marathon-lb")
        )

        assert_that(fake_mlb.check_signalled_usr1(), Is(True))

        # Signalled flag should be reset to false after it is checked
        assert_that(fake_mlb.check_signalled_usr1(), Is(False))
