# data_fetchers/xrpl_fetcher.py — XRP Ledger WebSocket 연동

import asyncio
import json
import httpx
from models import ChainPortfolio, StakingPosition, DeFiPosition
from data_fetchers.price_feed import get_token_price_usd

try:
    import xrpl
    from xrpl.asyncio.clients import AsyncWebsocketClient
    from xrpl.models.requests import AccountInfo, AccountLines, AccountOffers
    XRPL_AVAILABLE = True
except ImportError:
    XRPL_AVAILABLE = False

XRPL_PUBLIC_API = "https://xrplcluster.com"
EARNXRP_API     = "https://api.earnxrp.io/v1"
XUMM_AMM_API    = "https://xumm.app/api/v1/platform/amm"


async def fetch_xrpl_portfolio(wallet_address: str, ws_url: str) -> ChainPortfolio:
    """
    XRP Ledger 포트폴리오 조회.
    - XRP 네이티브 잔액
    - earnXRP 스테이킹 포지션
    - AMM LP 포지션
    """
    try:
        xrp_price, xrp_balance, staking_info, amm_positions = await asyncio.gather(
            get_token_price_usd("XRP"),
            _get_xrp_balance(wallet_address, ws_url),
            _get_earnxrp_position(wallet_address),
            _get_amm_positions(wallet_address, ws_url),
        )

        staking = []
        if staking_info["staked_amount"] > 0:
            staking.append(StakingPosition(
                protocol="earnXRP",
                asset="XRP",
                staked_amount=staking_info["staked_amount"],
                current_apy=staking_info["apy"],
                rewards_earned=staking_info["rewards_earned"],
                unlock_date=staking_info.get("unlock_date"),
            ))

        staking_value = staking_info["staked_amount"] * xrp_price
        amm_value = sum(p.value_usd for p in amm_positions)
        total_usd = (xrp_balance * xrp_price) + staking_value + amm_value

        return ChainPortfolio(
            chain="XRP",
            wallet_address=wallet_address,
            native_balance=xrp_balance,
            native_price_usd=xrp_price,
            total_value_usd=total_usd,
            staking_positions=staking,
            defi_positions=amm_positions,
        )

    except Exception as e:
        return ChainPortfolio(
            chain="XRP", wallet_address=wallet_address,
            native_balance=0, native_price_usd=0, total_value_usd=0,
            fetch_error=f"XRPL 조회 실패: {type(e).__name__}: {e}"
        )


# ── 내부 헬퍼 ────────────────────────────────────────────────────────

async def _get_xrp_balance(address: str, ws_url: str) -> float:
    """XRP 네이티브 잔액 조회 (drops → XRP)"""
    if XRPL_AVAILABLE:
        return await _xrpl_py_get_balance(address, ws_url)
    return await _ws_get_balance(address, ws_url)


async def _xrpl_py_get_balance(address: str, ws_url: str) -> float:
    async with AsyncWebsocketClient(ws_url) as client:
        req = AccountInfo(account=address, ledger_index="validated")
        resp = await client.request(req)
        balance_drops = int(resp.result["account_data"]["Balance"])
        return balance_drops / 10**6  # drops → XRP


async def _ws_get_balance(address: str, ws_url: str) -> float:
    """xrpl-py 미설치 시 XRPL HTTP API 직접 호출"""
    payload = {
        "method": "account_info",
        "params": [{"account": address, "ledger_index": "validated"}]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(XRPL_PUBLIC_API, json=payload)
        resp.raise_for_status()
        data = resp.json()
        balance_drops = int(data["result"]["account_data"]["Balance"])
        return balance_drops / 10**6


async def _get_earnxrp_position(wallet: str) -> dict:
    """earnXRP API에서 스테이킹 포지션 조회"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{EARNXRP_API}/staking/{wallet}")
            resp.raise_for_status()
            data = resp.json()

        return {
            "staked_amount": float(data.get("stakedAmount", 0)),
            "apy": float(data.get("apy", 0)),
            "rewards_earned": float(data.get("rewardsEarned", 0)),
            "unlock_date": data.get("unlockDate"),
        }
    except Exception:
        return {
            "staked_amount": 0.0,
            "apy": 0.0,
            "rewards_earned": 0.0,
            "unlock_date": None,
        }


async def _get_amm_positions(wallet: str, ws_url: str) -> list[DeFiPosition]:
    """XRPL AMM LP 포지션 조회 (account_lines에서 LP 토큰 탐지)"""
    try:
        trust_lines = await _get_trust_lines(wallet, ws_url)
        xrp_price = await get_token_price_usd("XRP")

        positions = []
        for line in trust_lines:
            # LP 토큰: currency가 hex 160-bit (AMM 풀 ID)
            currency = line.get("currency", "")
            balance = float(line.get("balance", 0))
            if len(currency) == 40 and balance > 0:
                # AMM 풀 조회로 실제 가치 계산
                pool_value = await _get_amm_pool_value(currency, ws_url)
                limit = float(line.get("limit", 1)) or 1
                share_pct = balance / limit
                value_usd = pool_value * share_pct

                positions.append(DeFiPosition(
                    protocol="XRPL AMM",
                    position_type="LP",
                    assets=[currency[:8] + "..."],  # LP 풀 ID 축약
                    value_usd=value_usd,
                    pnl_usd=0.0,
                    share_pct=share_pct,
                ))
        return positions
    except Exception:
        return []


async def _get_trust_lines(address: str, ws_url: str) -> list[dict]:
    if XRPL_AVAILABLE:
        async with AsyncWebsocketClient(ws_url) as client:
            req = AccountLines(account=address, ledger_index="validated")
            resp = await client.request(req)
            return resp.result.get("lines", [])

    payload = {
        "method": "account_lines",
        "params": [{"account": address, "ledger_index": "validated"}]
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(XRPL_PUBLIC_API, json=payload)
        resp.raise_for_status()
        return resp.json().get("result", {}).get("lines", [])


async def _get_amm_pool_value(pool_id: str, ws_url: str) -> float:
    """XRPL AMM 풀 총 유동성 USD 가치 조회"""
    try:
        payload = {
            "method": "amm_info",
            "params": [{"amm_account": pool_id, "ledger_index": "validated"}]
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(XRPL_PUBLIC_API, json=payload)
            resp.raise_for_status()
            amm = resp.json().get("result", {}).get("amm", {})

        xrp_drops = 0
        asset2_amount = 0.0
        amount1 = amm.get("amount")
        amount2 = amm.get("amount2")

        if isinstance(amount1, str):
            xrp_drops = int(amount1)
        elif isinstance(amount2, str):
            xrp_drops = int(amount2)

        xrp_amount = xrp_drops / 10**6
        xrp_price = await get_token_price_usd("XRP")
        # 양쪽 자산의 합 ≈ 2 * XRP side (단순화)
        return xrp_amount * xrp_price * 2
    except Exception:
        return 0.0
