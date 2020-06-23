import json
import os
from contextlib import asynccontextmanager, contextmanager
from functools import partial
from pathlib import Path
from subprocess import PIPE

import trio

from .subproc_sync import run_trio_in_subproc


def get_pebble_cmd():
    return os.environ.get("PEBBLE_CMD", "pebble")


def get_pebblects_cmd():
    return os.environ.get("PEBBLE_CHALLTESTSRV_CMD", "pebble-challtestsrv")


def get_certs_path():
    return os.environ.get("PEBBLE_CERTS_DIR", "test/certs")


async def send_lines(stream, send):
    buf = b""
    async for chunk in stream:
        lines = (buf + chunk).split(b"\n")
        buf = lines.pop()
        for line in lines:
            await send.send(line)
    await send.aclose()


class PebbleProc:
    """
    Runs a pebble command in a subprocess.
    """

    def __init__(self, cmd_path):
        self._cmd_path = cmd_path
        self._proc = None
        self.started = trio.Event()

    async def start(self, nursery, args, env=None):
        assert self._proc is None
        cmd = [self._cmd_path, *args]
        if env is not None and "PATH" not in env:
            env["PATH"] = os.environ["PATH"]
        self._proc = await trio.open_process(cmd, env=env, stdout=PIPE)
        self._tx_out_lines, self.out_lines = trio.open_memory_channel(0)
        nursery.start_soon(send_lines, self._proc.stdout, self._tx_out_lines)

    async def _watch_stdout(self, started_line):
        failed_lines = [b"bind: address already in use"]
        async for line in self.out_lines:
            if line.endswith(started_line):
                self.started.set()
            for failed_line in failed_lines:
                if line.endswith(failed_line):
                    raise RuntimeError(line)  # pragma: no cover

    async def wait(self, nursery, started_line):
        nursery.start_soon(self._watch_stdout, started_line)
        await self.started.wait()

    async def stop(self):
        self._proc.terminate()
        return await self._proc.wait()


class Pebble:
    """
    Run pebble and pebble-chaltestsrv in subprocesses.
    """

    def __init__(self, cfg_path):
        self.cfg_path = cfg_path
        self._pebble = PebbleProc(get_pebble_cmd())
        self._pcts = PebbleProc(get_pebblects_cmd())

    def _pcts_args(self, **opts):
        opts.setdefault("dns01", "127.0.0.1:8053")
        args = []
        for opt in ["http01", "https01", "tlsalpn01", "dns01", "defaultIPv6"]:
            args.extend([f"-{opt}", opts.pop(opt, "")])
        assert opts == {}
        return args

    async def start(self, nursery, always_valid=False):
        # Start subprocesses
        await self._pcts.start(nursery, self._pcts_args())
        pbl_args = ["-dnsserver", "127.0.0.1:8053", "-config", self.cfg_path]
        pbl_env = {"PEBBLE_VA_NOSLEEP": "1"}
        if always_valid:
            pbl_env["PEBBLE_VA_ALWAYS_VALID"] = "1"
        await self._pebble.start(nursery, pbl_args, pbl_env)
        # Wait for subprocesses to be ready
        await self._pcts.wait(nursery, b"Starting challenge servers")
        await self._pebble.wait(
            nursery, b"ACME directory available at: https://0.0.0.0:14000/dir"
        )
        self.dir_url = "https://127.0.0.1:14000/dir"

    async def stop(self):
        await self._pebble.stop()
        await self._pcts.stop()


def write_pebble_config(cfg_path):
    certs_path = Path(get_certs_path())
    cfg = {
        "pebble": {
            "listenAddress": "0.0.0.0:14000",
            "managementListenAddress": "0.0.0.0:15000",
            "certificate": str(certs_path / "localhost/cert.pem"),
            "privateKey": str(certs_path / "localhost/key.pem"),
            "httpPort": 5002,
            "tlsPort": 5001,
            "ocspResponderURL": "",
            "externalAccountBindingRequired": False,
        }
    }
    cfg_path.write_text(json.dumps(cfg))


@asynccontextmanager
async def bg_pebble(nursery, tmp_path, **opts):
    cfg_path = tmp_path / "pebble-config.json"
    write_pebble_config(cfg_path)
    pebble = Pebble(cfg_path)
    await pebble.start(nursery, **opts)
    try:
        yield pebble
    finally:
        await pebble.stop()


async def _start_pebble(pbl, task_status=trio.TASK_STATUS_IGNORED, **opts):
    async with trio.open_nursery() as nursery:
        await pbl.start(nursery, **opts)
        task_status.started()


@contextmanager
def bg_pebble_sync(tmp_path, **opts):
    cfg_path = tmp_path / "pebble-config.json"
    write_pebble_config(cfg_path)
    pebble = Pebble(cfg_path)
    start_f = partial(_start_pebble, pebble, **opts)
    with run_trio_in_subproc(start_f, pebble.stop):
        yield pebble
