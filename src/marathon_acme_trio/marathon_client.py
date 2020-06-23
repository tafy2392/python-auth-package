import json
import logging
import random
from contextlib import asynccontextmanager

import httpx
import trio
from marathon.exceptions import MarathonError  # type: ignore
from marathon.models.events import EventFactory  # type: ignore


class MarathonClient(object):
    def __init__(self, apis):
        self.apis = [a.rstrip("/") for a in apis]
        self.log = logging.getLogger(__name__.split(".")[0])

    def _get_api(self):
        return random.choice(self.apis)

    @asynccontextmanager
    async def _client(self):
        async with httpx.AsyncClient(base_url=self._get_api()) as client:
            yield client

    # maybe use a retry decorator?
    async def stream_events(self, events_tx, event_types=None):
        while True:
            try:
                await self._stream_events(events_tx, event_types)
            except Exception:
                self.log.exception("Failed to stream from server")
                await trio.sleep(2)

    async def _stream_events(self, events_tx, event_types):
        ef = EventFactory()
        async with self._client() as client:
            headers = {"Accept": "text/event-stream"}
            params = {}
            if event_types:
                params["event_type"] = event_types
            async with client.stream(
                "GET", "/v2/events", headers=headers, params=params, timeout=60
            ) as response:
                async for line in response.aiter_lines():
                    _data = line.split(":", 1)
                    if _data[0] == "data":
                        event_data = json.loads(_data[1].strip())
                        if "eventType" not in event_data:
                            raise MarathonError("Invalid event data received.")
                        event = ef.process(event_data)
                        if event_types and event.event_type not in event_types:
                            # We're filtering events, but got an unwanted one
                            # anyway. Ignore it and move on.
                            continue
                        self.log.info(f"Received marathon event: {event}")
                        await events_tx.send(event)

    async def event_stream(self, event_types=None, *, task_status):
        events_tx, events_rx = trio.open_memory_channel(0)
        task_status.started(events_rx)
        await self.stream_events(events_tx, event_types)

    # TODO: retry alternate urls
    async def get_apps(self):
        async with self._client() as client:
            response = await client.get("/v2/apps")
            return response.json()["apps"]
