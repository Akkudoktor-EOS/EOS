"""Task handling taken from fastapi-utils/fastapi_utils/tasks.py."""

from __future__ import annotations

import asyncio
from functools import wraps
from typing import Any, Callable, Coroutine, Union

import loguru
from starlette.concurrency import run_in_threadpool

NoArgsNoReturnFuncT = Callable[[], None]
NoArgsNoReturnAsyncFuncT = Callable[[], Coroutine[Any, Any, None]]
ExcArgNoReturnFuncT = Callable[[Exception], None]
ExcArgNoReturnAsyncFuncT = Callable[[Exception], Coroutine[Any, Any, None]]
NoArgsNoReturnAnyFuncT = Union[NoArgsNoReturnFuncT, NoArgsNoReturnAsyncFuncT]
ExcArgNoReturnAnyFuncT = Union[ExcArgNoReturnFuncT, ExcArgNoReturnAsyncFuncT]
NoArgsNoReturnDecorator = Callable[[NoArgsNoReturnAnyFuncT], NoArgsNoReturnAsyncFuncT]


async def _handle_func(func: NoArgsNoReturnAnyFuncT) -> None:
    if asyncio.iscoroutinefunction(func):
        await func()
    else:
        await run_in_threadpool(func)


async def _handle_exc(exc: Exception, on_exception: ExcArgNoReturnAnyFuncT | None) -> None:
    if on_exception:
        if asyncio.iscoroutinefunction(on_exception):
            await on_exception(exc)
        else:
            await run_in_threadpool(on_exception, exc)


def repeat_every(
    *,
    seconds: float,
    wait_first: float | None = None,
    logger: loguru.logger | None = None,
    raise_exceptions: bool = False,
    max_repetitions: int | None = None,
    on_complete: NoArgsNoReturnAnyFuncT | None = None,
    on_exception: ExcArgNoReturnAnyFuncT | None = None,
) -> NoArgsNoReturnDecorator:
    """A decorator that modifies a function so it is periodically re-executed after its first call.

    The function it decorates should accept no arguments and return nothing. If necessary, this can be accomplished
    by using `functools.partial` or otherwise wrapping the target function prior to decoration.

    Parameters
    ----------
    seconds: float
        The number of seconds to wait between repeated calls
    wait_first: float (default None)
        If not None, the function will wait for the given duration before the first call
    max_repetitions: Optional[int] (default None)
        The maximum number of times to call the repeated function. If `None`, the function is repeated forever.
    on_complete: Optional[Callable[[], None]] (default None)
        A function to call after the final repetition of the decorated function.
    on_exception: Optional[Callable[[Exception], None]] (default None)
        A function to call when an exception is raised by the decorated function.
    """

    def decorator(func: NoArgsNoReturnAnyFuncT) -> NoArgsNoReturnAsyncFuncT:
        """Converts the decorated function into a repeated, periodically-called version."""

        @wraps(func)
        async def wrapped() -> None:
            async def loop() -> None:
                if wait_first is not None:
                    await asyncio.sleep(wait_first)

                repetitions = 0
                while max_repetitions is None or repetitions < max_repetitions:
                    try:
                        await _handle_func(func)

                    except Exception as exc:
                        await _handle_exc(exc, on_exception)

                    repetitions += 1
                    await asyncio.sleep(seconds)

                if on_complete:
                    await _handle_func(on_complete)

            asyncio.ensure_future(loop())

        return wrapped

    return decorator


def make_repeated_task(
    func: NoArgsNoReturnAnyFuncT,
    *,
    seconds: float,
    wait_first: float | None = None,
    max_repetitions: int | None = None,
    on_complete: NoArgsNoReturnAnyFuncT | None = None,
    on_exception: ExcArgNoReturnAnyFuncT | None = None,
) -> NoArgsNoReturnAsyncFuncT:
    """Create a version of the given function that runs periodically.

    This function wraps `func` with the `repeat_every` decorator at runtime,
    allowing decorator parameters to be determined dynamically rather than at import time.

    Args:
        func (Callable[[], None] | Callable[[], Coroutine[Any, Any, None]]):
            The function to execute periodically. Must accept no arguments.
        seconds (float):
            Interval in seconds between repeated calls.
        wait_first (float | None, optional):
            If provided, the function will wait this many seconds before the first call.
        max_repetitions (int | None, optional):
            Maximum number of times to repeat the function. If None, repeats indefinitely.
        on_complete (Callable[[], None] | Callable[[], Coroutine[Any, Any, None]] | None, optional):
            Function to call once the repetitions are complete.
        on_exception (Callable[[Exception], None] | Callable[[Exception], Coroutine[Any, Any, None]] | None, optional):
            Function to call if an exception is raised by `func`.

    Returns:
        Callable[[], Coroutine[Any, Any, None]]:
            An async function that starts the periodic execution when called.

    Usage:
        .. code-block:: python

            from my_task import my_task

            from akkudoktoreos.core.coreabc import get_config
            from akkudoktoreos.server.rest.tasks import make_repeated_task

            config = get_config()

            # Create a periodic task using configuration-dependent interval
            repeated_task = make_repeated_task(
                my_task,
                seconds=config.server.poll_interval,
                wait_first=5,
                max_repetitions=None
            )

            # Run the task in the event loop
            import asyncio
            asyncio.run(repeated_task())


    Notes:
        - This pattern avoids starting the loop at import time.
        - Arguments such as `seconds` can be read from runtime sources (config, CLI args, environment variables).
        - The returned function must be awaited to start the periodic loop.
    """
    # Return decorated function
    return repeat_every(
        seconds=seconds,
        wait_first=wait_first,
        max_repetitions=max_repetitions,
        on_complete=on_complete,
        on_exception=on_exception,
    )(func)
