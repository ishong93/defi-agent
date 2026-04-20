# data_fetchers/_helpers.py — 공통 헬퍼
#
# 핵심 원칙: "실제 키/주소가 있으면 실제 호출, 없거나 실패하면 mock으로 폴백"
# 이로 인해 e2e 테스트(placeholder 주소)와 프로덕션(실제 주소) 모두 동작한다.

from __future__ import annotations
import asyncio
import time
from logger import setup_logger

log = setup_logger("fetchers")


def is_placeholder_wallet(addr: str) -> bool:
    """
    실제 EVM/XDC 주소가 아닌 placeholder(테스트용, 미설정 기본값)인지 판정.
    유효한 주소: 0x + 40 hex (EVM) 또는 xdc + 40 hex.
    """
    if not addr:
        return True
    if addr.startswith("0x"):
        hex_part = addr[2:]
    elif addr.startswith("xdc"):
        hex_part = addr[3:]
    else:
        return True
    if len(hex_part) != 40:
        return True
    try:
        int(hex_part, 16)
        return False
    except ValueError:
        return True


# ── 간단한 TTL 캐시 (가격 조회용) ─────────────────────────────────
# CoinGecko 무료 티어는 분당 30회 제한. 중복 호출을 방지한다.

_cache: dict[str, tuple[float, float]] = {}  # key → (value, expires_at)


def cache_get(key: str) -> float | None:
    hit = _cache.get(key)
    if hit and hit[1] > time.time():
        return hit[0]
    return None


def cache_set(key: str, value: float, ttl_seconds: float = 300) -> None:
    _cache[key] = (value, time.time() + ttl_seconds)


async def run_blocking(fn, *args, **kwargs):
    """동기 I/O를 별도 스레드에서 실행 (requests, web3 동기 호출용)."""
    return await asyncio.to_thread(fn, *args, **kwargs)
