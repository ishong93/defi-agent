# data_fetchers/xdc_fetcher.py — XDC Network + PrimeStaking

import asyncio
import httpx
from models import ChainPortfolio, StakingPosition
from data_fetchers.price_feed import get_token_price_usd

try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False

PRIMESTAKING_API = "https://primestaking.net/api"


async def fetch_xdc_portfolio(wallet_address: str, rpc_url: str) -> ChainPortfolio:
    """
    XDC Network 포트폴리오 조회.
    PrimeStaking: XDC 위임 스테이킹 + Voter Delegation 보상
    """
    try:
        xdc_price, xdc_balance, staking_info = await asyncio.gather(
            get_token_price_usd("XDC"),
            _get_xdc_balance(wallet_address, rpc_url),
            _get_primestaking_position(wallet_address),
        )

        staking = []
        if staking_info["delegated_amount"] > 0:
            staking.append(StakingPosition(
                protocol="PrimeStaking",
                asset="XDC",
                staked_amount=staking_info["delegated_amount"],
                current_apy=staking_info["apy"],
                rewards_earned=staking_info["pending_rewards"],
                unlock_date=staking_info.get("epoch_end"),
            ))

        total_usd = (xdc_balance + staking_info["delegated_amount"]) * xdc_price

        return ChainPortfolio(
            chain="XDC", wallet_address=wallet_address,
            native_balance=xdc_balance, native_price_usd=xdc_price,
            total_value_usd=total_usd, staking_positions=staking,
        )
    except Exception as e:
        return ChainPortfolio(
            chain="XDC", wallet_address=wallet_address,
            native_balance=0, native_price_usd=0, total_value_usd=0,
            fetch_error=f"XDC 조회 실패: {type(e).__name__}: {e}"
        )


# ── 내부 헬퍼 ────────────────────────────────────────────────────────

async def _get_xdc_balance(address: str, rpc_url: str) -> float:
    """XDC 네이티브 잔액 조회 (wei → XDC)"""
    if WEB3_AVAILABLE:
        return await asyncio.get_event_loop().run_in_executor(
            None, _web3_get_balance, address, rpc_url
        )
    return await _rpc_get_balance(address, rpc_url)


def _web3_get_balance(address: str, rpc_url: str) -> float:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    # XDC 주소는 'xdc' 접두사 → '0x'로 변환
    eth_address = _xdc_to_eth_address(address)
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(eth_address))
    return float(Web3.from_wei(balance_wei, "ether"))


async def _rpc_get_balance(address: str, rpc_url: str) -> float:
    eth_address = _xdc_to_eth_address(address)
    payload = {"jsonrpc": "2.0", "method": "eth_getBalance",
               "params": [eth_address, "latest"], "id": 1}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        hex_balance = resp.json()["result"]
        return int(hex_balance, 16) / 10**18


async def _get_primestaking_position(wallet: str) -> dict:
    """PrimeStaking REST API에서 위임 정보 조회"""
    eth_address = _xdc_to_eth_address(wallet)
    url = f"{PRIMESTAKING_API}/delegator/{eth_address}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()

        return {
            "delegated_amount": float(data.get("delegatedAmount", 0)),
            "apy": float(data.get("apy", 0)),
            "pending_rewards": float(data.get("pendingRewards", 0)),
            "epoch_end": data.get("epochEnd"),
            "masternode": data.get("masternode", ""),
        }
    except Exception:
        return {
            "delegated_amount": 0.0,
            "apy": 0.0,
            "pending_rewards": 0.0,
            "epoch_end": None,
            "masternode": "",
        }


def _xdc_to_eth_address(address: str) -> str:
    """XDC 주소 형식(xdc...)을 EVM 형식(0x...)으로 변환"""
    if address.lower().startswith("xdc"):
        return "0x" + address[3:]
    return address
