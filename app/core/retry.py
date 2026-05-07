"""Async retry helper with exponential backoff and structured logging.

Wraps `tenacity` so the orchestrator gets uniform retry semantics for every
adapter call (snapshot, stop, boot, ...). Each retry is logged with the
attempt number and label so the audit trail and operator dashboards can
observe transient failures.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger("app.retry")

T = TypeVar("T")


async def with_retry(
    func: Callable[..., Awaitable[T]],
    *args: Any,
    label: str = "operation",
    max_attempts: int = 3,
    initial_wait: float = 0.5,
    max_wait: float = 8.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
    **kwargs: Any,
) -> T:
    """Run `func(*args, **kwargs)` with exponential backoff.

    Re-raises the original exception on final failure (RetryError unwrapped)
    so the caller's exception handler sees the real cause.
    """
    attempt_n = 0
    try:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=initial_wait, max=max_wait),
            retry=retry_if_exception_type(retry_on),
            reraise=True,
        ):
            with attempt:
                attempt_n = attempt.retry_state.attempt_number
                logger.debug(
                    "retry.attempt", extra={"label": label, "attempt": attempt_n}
                )
                return await func(*args, **kwargs)
    except RetryError as exc:  # pragma: no cover - reraise=True bypasses this
        if exc.last_attempt and exc.last_attempt.exception() is not None:
            raise exc.last_attempt.exception()  # type: ignore[misc]
        raise
    raise RuntimeError(f"retry loop exited without result for {label}")  # pragma: no cover
