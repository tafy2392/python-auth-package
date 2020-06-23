import json
from contextlib import closing, contextmanager
from datetime import datetime
from functools import partial

import trio
from quart_trio import QuartTrio

from .subproc_sync import run_trio_in_subproc


def marathon_timestamp():
    """
    Make a Marathon/JodaTime-like timestamp string in ISO8601 format with
    milliseconds for the current time in UTC.
    """
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class FakeMarathonBackend:
    def __init__(self, filter_events=True):
        self._apps = {}
        self.event_chans = {}
        self._filter_events = filter_events

    async def add_app(self, app, client_ip=None):
        # Store the app
        app_id = app["id"]
        assert app_id not in self._apps
        self._apps[app_id] = app

        await self.trigger_event(
            "api_post_event",
            clientIp=client_ip,
            uri="/v2/apps/" + app_id.lstrip("/"),
            appDefinition=app,
        )

    def get_apps(self):
        return list(self._apps.values())

    async def attach_event_stream(
        self, chan, event_types=None, remote_address=None
    ):
        assert chan not in self.event_chans

        self.event_chans[chan] = event_types
        await self.trigger_event(
            "event_stream_attached", remoteAddress=remote_address
        )

    async def detach_event_stream(self, chan, remote_address=None):
        assert chan in self.event_chans
        self.event_chans.pop(chan)
        await self.trigger_event(
            "event_stream_detached", remoteAddress=remote_address
        )

    def _should_emit_event(self, event_type, event_types):
        if not self._filter_events:
            return True
        elif event_types and event_type not in event_types:
            return False
        else:
            return True

    async def trigger_event(self, event_type, **kwargs):
        event = {"eventType": event_type, "timestamp": marathon_timestamp()}
        event.update(kwargs)

        for chan, event_types in self.event_chans.items():
            if self._should_emit_event(event_type, event_types):
                await chan.send(event)


def _make_app_marathon(marathon):
    from quart import jsonify, make_response, request

    app = QuartTrio(__name__)
    backend = marathon._backend

    @app.route("/v2/apps", methods=["GET"])
    async def get_apps():
        marathon._called_get_apps = True
        return jsonify({"apps": backend.get_apps()})

    @app.route("/v2/apps", methods=["POST"])
    async def post_apps():
        app = await request.json
        await backend.add_app(app)
        return jsonify(app)

    @app.route("/v2/events", methods=["GET"])
    async def get_events():
        assert request.headers["Accept"] == "text/event-stream"
        remote_addr = request.remote_addr
        event_types = request.args.getlist("event_type")

        # Ideally we'd use a zero-size buffer, but we need to send the attach
        # event before we return the response.
        send, recv = trio.open_memory_channel(1)
        await backend.attach_event_stream(send, event_types, remote_addr)

        async def forward_events():
            try:
                async for event in recv:
                    event_type = event["eventType"]
                    msg = f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
                    yield msg.encode("utf-8")
            except trio.Cancelled:
                # FIXME: We don't seem to get cancelled on disconnection here,
                # see https://gitlab.com/pgjones/hypercorn/-/issues/124 for
                # details.
                raise
            finally:
                await backend.detach_event_stream(send, remote_addr)

        headers = {"Content-Type": "text/event-stream"}
        return await make_response(forward_events(), headers)

    return app


def aclosing_contextmanager(cls):
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    cls.__aenter__ = __aenter__
    cls.__aexit__ = __aexit__
    return cls


def _app_client(app, base_url, client_ip):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app, client=(client_ip, 12345))
    return AsyncClient(transport=transport, base_url=base_url)


@aclosing_contextmanager
class FakeMarathon:
    def __init__(self, backend=None, filter_events=True):
        if backend is None:
            backend = FakeMarathonBackend(filter_events=filter_events)
        self._backend = backend
        self._app = _make_app_marathon(self)
        self.event_requests = []
        self._called_get_apps = False
        self._clients = []

    async def aclose(self):
        while self._clients:
            await self._clients.pop().aclose()

    def get_client(self, client_ip="127.0.0.1"):
        client = _app_client(self._app, "http://fake_marathon", client_ip)
        self._clients.append(client)
        return client

    def get_http_client(self):
        from httpx import AsyncClient

        client = AsyncClient(base_url=self.base_url)
        self._clients.append(client)
        return client

    def check_called_get_apps(self):
        """
        Check and reset the ``_called_get_apps`` flag.
        """
        was_called, self._called_get_apps = self._called_get_apps, False
        return was_called

    async def add_app(self, app, client_ip=None):
        await self._backend.add_app(app, client_ip)

    async def add_apps(self, *apps):
        for app in apps:
            await self.add_app(app)

    async def start_http(self, nursery, port=0):
        from hypercorn.config import Config
        from hypercorn.trio import serve

        config = Config()
        config.bind = [f"localhost:{port}"]
        [base_url] = await nursery.start(serve, self._app, config)
        self.base_url = base_url
        return base_url


def _make_app_mlb(mlb):
    from quart import make_response

    app = QuartTrio(__name__)

    async def plain_resp(body):
        resp = await make_response(body)
        resp.content_type = "text/plain"
        return resp

    @app.route("/_mlb_signal/hup", methods=["POST"])
    async def signal_hup():
        mlb._signalled_hup = True
        return await plain_resp("Sent SIGHUP signal to marathon-lb")

    @app.route("/_mlb_signal/usr1", methods=["POST"])
    async def signal_usr1():
        mlb._signalled_usr1 = True
        return await plain_resp("Sent SIGUSR1 signal to marathon-lb")

    return app


@aclosing_contextmanager
class FakeMarathonLb:
    def __init__(self):
        self._app = _make_app_mlb(self)
        self._signalled_hup = False
        self._signalled_usr1 = False
        self._clients = []

    async def aclose(self):
        while self._clients:
            await self._clients.pop().aclose()

    def get_http_client(self):
        from httpx import AsyncClient

        client = AsyncClient(base_url=self.base_url)
        self._clients.append(client)
        return client

    def check_signalled_hup(self):
        """
        Check and reset the ``_signalled_hup`` flag.
        """
        was_signalled, self._signalled_hup = self._signalled_hup, False
        return was_signalled

    def check_signalled_usr1(self):
        """
        Check and reset the ``_signalled_usr1`` flag.
        """
        was_signalled, self._signalled_usr1 = self._signalled_usr1, False
        return was_signalled

    async def start_http(self, nursery, port=0):
        from hypercorn.config import Config
        from hypercorn.trio import serve

        config = Config()
        config.bind = [f"localhost:{port}"]
        [base_url] = await nursery.start(serve, self._app, config)
        self.base_url = base_url
        return base_url


class FakeMarathonSubprocWrapper:
    async def run(self, task_status=trio.TASK_STATUS_IGNORED, **opts):
        self._fm = FakeMarathon()
        async with trio.open_nursery() as nursery:
            self._nursery = nursery
            base_url = await self._fm.start_http(nursery, **opts)
            task_status.started(base_url)

    async def stop(self):
        self._nursery.cancel_scope.cancel()


class FakeMarathonClientWrapperSync:
    def __init__(self, base_url):
        self.base_url = base_url
        self._clients = []
        self._client = self.get_http_client()

    def close(self):
        while self._clients:
            self._clients.pop().close()

    def get_http_client(self):
        from httpx import Client

        client = Client(base_url=self.base_url)
        self._clients.append(client)
        return client

    def add_app(self, app):
        return self._client.post("/v2/apps", json=app)

    def add_apps(self, *apps):
        for app in apps:
            self.add_app(app)


@contextmanager
def bg_fake_marathon_sync(**opts):
    fmsw = FakeMarathonSubprocWrapper()
    start_f = partial(fmsw.run, **opts)
    with run_trio_in_subproc(start_f, fmsw.stop) as base_url:
        with closing(FakeMarathonClientWrapperSync(base_url)) as fm:
            yield fm
