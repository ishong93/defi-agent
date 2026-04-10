# retry.py — 지수 백오프 재시도 (RPC 불안정 대응)

import asyncio
import functools
import time
from typing import Callable, Type
from logger import setup_logger

log = setup_logger("retry")


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
):
    """
    지수 백오프 재시도 데코레이터.
    동기/비동기 함수 모두 지원.

    사용 예:
        @with_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
        async def fetch_flare_balance(wallet):
            ...
    """
    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        log.error(f"{fn.__name__} 최종 실패 (시도 {attempt}/{max_attempts})",
                                  extra={"fn": fn.__name__, "attempt": attempt, "error": str(e)})
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    log.warning(f"{fn.__name__} 실패 → {delay:.1f}초 후 재시도 ({attempt}/{max_attempts}): {e}",
                                extra={"fn": fn.__name__, "attempt": attempt, "delay": delay})
                    await asyncio.sleep(delay)

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    time.sleep(delay)

        return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper
    return decorator
