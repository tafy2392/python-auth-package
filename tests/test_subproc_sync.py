import time

import pytest  # type: ignore
import trio

from .subproc_sync import run_trio_in_subproc

# We can't run closures in our subprocess, because they can't be pickled.
# Instead, we use various standalone functions and classes.


async def start_simple(task_status):
    await trio.sleep(0.001)
    task_status.started()


async def start_value(task_status):
    await trio.sleep(0.001)
    task_status.started(42)


async def start_err(task_status):
    await trio.sleep(0.001)
    raise Exception("KABLOOEY")


async def stop_noop():
    pass


class LongRunning:
    def __init__(self, runtime):
        self.runtime = runtime

    async def start(self, task_status):
        await trio.sleep(0.001)
        self.cs = trio.CancelScope()
        task_status.started()
        with self.cs:
            await trio.sleep(self.runtime)

    async def stop(self):
        await trio.sleep(0.001)
        self.cs.cancel()


def test_wait_for_startup():
    """
    We wait for the subprocess to finish its startup before returning.
    """

    with run_trio_in_subproc(start_simple, stop_noop) as value:
        assert value is None


def test_wait_for_startup_value():
    """
    At the end of its startup, a subprocess can return a value to us.
    """

    with run_trio_in_subproc(start_value, stop_noop) as value:
        assert value == 42


def test_wait_for_startup_exception():
    """
    If subprocess startup raises an exception, we raise our own referencing it.
    """

    with pytest.raises(RuntimeError) as errinfo:
        with run_trio_in_subproc(start_err, stop_noop):
            pass  # pragma: no cover

    assert str(errinfo.value) == "Exception in subprocess: KABLOOEY"


def test_wait_for_shutdown():
    """
    A long-running process is stopped with its stop function when we exit the
    context manager block.
    """

    lr = LongRunning(10)
    before = time.monotonic()

    with run_trio_in_subproc(lr.start, lr.stop):
        pass

    after = time.monotonic()
    assert after - before < 10
