import logging
from dataclasses import dataclass
from typing import Dict

from quart_trio import QuartTrio

log = logging.getLogger()


def mk_app(challenges):
    app = QuartTrio(__name__)

    @app.route("/.well-known/acme-challenge/<ch_code>")
    async def get_challenge(ch_code):
        log.debug(f"Got challenge request for {ch_code}")
        return challenges.get(ch_code) or ("Not Found", 404)

    @app.route("/ping")
    async def ping():
        return "pong"

    return app


@dataclass
class HTTP01Server:
    app: QuartTrio
    challenges: Dict[str, str]

    @classmethod
    def build(cls):
        challenges = {}
        return cls(mk_app(challenges), challenges)

    async def receive_challenges(self, challenges_rx):
        async for challenge in challenges_rx:
            if challenge.content is None:
                del self.challenges[challenge.id]
            else:
                self.challenges[challenge.id] = challenge.content

    async def start_http(self, nursery, bind_host="0.0.0.0", port=8000):
        from hypercorn.config import Config
        from hypercorn.trio import serve

        config = Config()
        config.bind = [f"{bind_host}:{port}"]
        await nursery.start(serve, self.app, config)

    async def start(self, nursery, chal_rx, bind_host="0.0.0.0", port=8000):
        nursery.start_soon(self.receive_challenges, chal_rx)
        await self.start_http(nursery, bind_host, port)
