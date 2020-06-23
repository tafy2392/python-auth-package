import pytest  # type: ignore
import trio
from testtools.assertions import assert_that  # type: ignore
from testtools.matchers import Equals  # type: ignore

from marathon_acme_trio.marathon_client import MarathonClient

from .fake_marathon import FakeMarathon
from .helpers import mkapp
from .matchers import is_marathon_event


@pytest.fixture
async def fake_marathon(nursery):
    async with FakeMarathon() as fake_marathon:
        await fake_marathon.start_http(nursery)
        yield fake_marathon


@pytest.fixture
async def fake_old_marathon(nursery):
    async with FakeMarathon(filter_events=False) as fake_marathon:
        await fake_marathon.start_http(nursery)
        yield fake_marathon


def is_attach_event(remote_address):
    return is_marathon_event(
        "event_stream_attached", remoteAddress=Equals(remote_address)
    )


def is_app_event(uri, app_dict):
    return is_marathon_event(
        "api_post_event", uri=Equals(uri), appDefinition=Equals(app_dict)
    )


class TestMarathonClient:
    async def test_get_apps_empty(self, fake_marathon):
        """
        We return an empty apps list when there are no apps to get.
        """
        mc = MarathonClient([fake_marathon.base_url])
        apps = await mc.get_apps()
        assert apps == []

    async def test_get_apps(self, fake_marathon):
        """
        We return a list of apps when there are apps to get.
        """
        app = mkapp("/my-app_1")
        await fake_marathon.add_app(app)

        mc = MarathonClient([fake_marathon.base_url])
        apps = await mc.get_apps()
        assert apps == [app]

    async def test_stream_events_attach(self, nursery, fake_marathon):
        """
        An unfiltered event stream always starts with an attach event.
        """
        mc = MarathonClient([fake_marathon.base_url])
        events_rx = await nursery.start(mc.event_stream)

        event = await events_rx.receive()
        assert_that(event.json_repr(), is_attach_event("127.0.0.1"))

    async def test_stream_events_app(self, nursery, fake_marathon):
        """
        An unfiltered event stream receives app events.
        """
        mc = MarathonClient([fake_marathon.base_url])
        events_rx = await nursery.start(mc.event_stream)

        event = await events_rx.receive()
        assert_that(event.json_repr(), is_attach_event("127.0.0.1"))

        app = mkapp("/my-app_1")
        await fake_marathon.add_app(app)

        event = await events_rx.receive()
        assert_that(event.json_repr(), is_app_event("/v2/apps/my-app_1", app))

    async def test_stream_filtered(self, nursery, fake_marathon):
        """
        A filtered event stream only receives events of the types it requests.
        """
        mc = MarathonClient([fake_marathon.base_url])
        events_rx = await nursery.start(mc.event_stream, ["api_post_event"])

        # No attach event (because we only asked for app events) so we need to
        # wait a bit for the connection to be established before we add the app
        # that will trigger the event we're expecting.
        await trio.sleep(0.01)

        app = mkapp("/my-app_1")
        await fake_marathon.add_app(app)

        event = await events_rx.receive()
        assert_that(event.json_repr(), is_app_event("/v2/apps/my-app_1", app))

    async def test_stream_filtered_old(self, nursery, fake_old_marathon):
        """
        Older marathon doesn't support filtering events, so we have to do it
        client-side as well.
        """
        mc = MarathonClient([fake_old_marathon.base_url])
        events_rx = await nursery.start(mc.event_stream, ["api_post_event"])

        # No attach event (because we only asked for app events) so we need to
        # wait a bit for the connection to be established before we add the app
        # that will trigger the event we're expecting.
        await trio.sleep(0.01)

        app = mkapp("/my-app_1")
        await fake_old_marathon.add_app(app)

        event = await events_rx.receive()
        assert_that(event.json_repr(), is_app_event("/v2/apps/my-app_1", app))
