# data_fetchers/price_feed.py — CoinGecko API 공통 가격 조회 모듈

import asyncio
import time
import httpx
from typing import Optional
import os

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_BASE = "https://pro-api.coingecko.com/api/v3"

COIN_IDS = {
    "FLR": "flare-networks",
    "XDC": "xdce-crowd-sale",
    "XRP": "ripple",
}

_cache: dict[str, tuple[float, float]] = {}  # token -> (price_usd, expires_at)
_CACHE_TTL = 60.0  # 1분 캐시


async def get_token_price_usd(symbol: str) -> float:
    """
    토큰 USD 가격 조회. 캐시 우선, 만료 시 CoinGecko API 재조회.
    symbol: "FLR" | "XDC" | "XRP"
    """
    now = time.monotonic()
    cached = _cache.get(symbol)
    if cached and cached[1] > now:
        return cached[0]

    coin_id = COIN_IDS.get(symbol)
    if not coin_id:
        raise ValueError(f"지원하지 않는 토큰: {symbol}")

    price = await _fetch_coingecko_price(coin_id)
    _cache[symbol] = (price, now + _CACHE_TTL)
    return price


async def get_all_prices() -> dict[str, float]:
    """FLR, XDC, XRP 가격을 병렬로 한번에 조회"""
    results = await asyncio.gather(
        *[get_token_price_usd(sym) for sym in COIN_IDS],
        return_exceptions=True
    )
    prices: dict[str, float] = {}
    for sym, result in zip(COIN_IDS.keys(), results):
        if isinstance(result, Exception):
            prices[sym] = 0.0
        else:
            prices[sym] = result
    return prices


async def _fetch_coingecko_price(coin_id: str) -> float:
    api_key = os.getenv("COINGECKO_API_KEY")
    base_url = COINGECKO_PRO_BASE if api_key else COINGECKO_BASE
    headers = {"x-cg-pro-api-key": api_key} if api_key else {}
    url = f"{base_url}/simple/price"
    params = {"ids": coin_id, "vs_currencies": "usd"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        price = data.get(coin_id, {}).get("usd")
        if price is None:
            raise ValueError(f"CoinGecko 응답에 {coin_id} 가격 없음: {data}")
        return float(price)
