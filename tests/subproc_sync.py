from contextlib import contextmanager
from multiprocessing import get_context

import trio

ctx = get_context("spawn")


async def _run_subproc(conn, start_f, stop_f):
    try:
        async with trio.open_nursery() as nursery:
            # Wait for startup.
            value = await nursery.start(start_f)
            # Tell parent process that we've started.
            await trio.to_thread.run_sync(conn.send, value)
            # Wait for parent process to tell us to stop.
            await trio.to_thread.run_sync(conn.recv)
            # Wait for shutdown.
            await stop_f()
    except Exception as e:
        # If we get an exception, we want to pass it up to our parent (who
        # might still be waiting for our start noticifation) and then call our
        # stop function to clean up. If our stop function raises an
        # exception... well, at least we tried.
        await trio.to_thread.run_sync(conn.send, e)
        await stop_f()
        raise


def _run_subproc_sync(conn, start_f, stop_f):
    trio.run(_run_subproc, conn, start_f, stop_f)


@contextmanager
def run_trio_in_subproc(start_f, stop_f):
    parent_conn, child_conn = ctx.Pipe()
    args = [child_conn, start_f, stop_f]
    p = ctx.Process(target=_run_subproc_sync, args=args)
    # Start child process.
    p.start()
    # Wait for child to tell us either that it's started or that it caught an
    # exception. We don't want to wait indefinitely, so we arbitrarily pick 30s
    # as a "reasonable" timeout. The Pipe API doesn't let us set a timeout on
    # recv() for some reason so we have to poll() first.
    if not parent_conn.poll(30):  # pragma: no cover
        raise RuntimeError("Timeout waiting for subprocess")
    value = parent_conn.recv()
    if isinstance(value, Exception):
        raise RuntimeError(f"Exception in subprocess: {value}")
    try:
        # Wait for the caller to do its thing.
        yield value
    finally:
        # Tell child process to stop.
        parent_conn.send(None)
        # Wait for child process to actually stop.
        p.join()
