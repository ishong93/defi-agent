# data_fetchers/price_provider.py — CoinGecko 가격 조회
#
# 무료 티어: 분당 30회. TTL 캐시로 중복 호출 방지.
# 실패 시 None 반환 → 호출자가 mock으로 폴백.

from __future__ import annotations
import requests
from ._helpers import cache_get, cache_set, run_blocking, log

_COINGECKO_IDS = {
    "FLR": "flare-networks",
    "XRP": "ripple",
    "XDC": "xdce-crowd-sale",
}

_COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"


def _fetch_sync(coin_id: str) -> float | None:
    try:
        r = requests.get(
            _COINGECKO_URL,
            params={"ids": coin_id, "vs_currencies": "usd"},
            timeout=5,
        )
        r.raise_for_status()
        return float(r.json()[coin_id]["usd"])
    except Exception as e:
        log.debug(f"CoinGecko {coin_id} 조회 실패: {type(e).__name__}: {e}")
        return None


async def fetch_price_usd(symbol: str) -> float | None:
    """심볼(FLR/XRP/XDC) → USD 가격. 캐시 히트 시 즉시 반환."""
    coin_id = _COINGECKO_IDS.get(symbol.upper())
    if not coin_id:
        return None
    cached = cache_get(f"price:{coin_id}")
    if cached is not None:
        return cached
    price = await run_blocking(_fetch_sync, coin_id)
    if price is not None:
        cache_set(f"price:{coin_id}", price, ttl_seconds=300)
    return price
