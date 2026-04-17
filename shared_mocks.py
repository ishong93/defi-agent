# shared_mocks.py — 공유 Mock 데이터
#
# tools.py와 agents/registry.py가 동일한 가격 히스토리를 별도로 정의했던 중복을
# 한 곳으로 모은 것. 실제 구현에서는 CoinGecko 등 외부 API 호출로 교체된다.

PRICE_HISTORY_7D: dict[str, list[float]] = {
    "FLR": [0.0192, 0.0188, 0.0185, 0.0179, 0.0183, 0.0186, 0.0185],
    "XDC": [0.0445, 0.0441, 0.0438, 0.0432, 0.0435, 0.0433, 0.0432],
    "XRP": [2.35,   2.28,   2.25,   2.19,   2.22,   2.28,   2.31],
}


def price_change_pct(asset: str) -> tuple[list[float], float]:
    """(가격 시리즈, 시작-끝 변동률%) 반환. 알 수 없는 자산이면 ([], 0.0)."""
    prices = PRICE_HISTORY_7D.get(asset, [])
    if not prices:
        return [], 0.0
    change = (prices[-1] - prices[0]) / prices[0] * 100
    return prices, change
