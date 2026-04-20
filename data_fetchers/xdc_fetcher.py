# data_fetchers/xdc_fetcher.py — XDC Network + PrimeStaking
#
# 실제 지갑 주소면 web3.py / CoinGecko 호출, placeholder거나 실패 시 mock 폴백.

from models import ChainPortfolio, StakingPosition
from ._helpers import is_placeholder_wallet, run_blocking, log
from .price_provider import fetch_price_usd


async def fetch_xdc_portfolio(wallet_address: str, rpc_url: str) -> ChainPortfolio:
    """
    XDC Network 포트폴리오 조회.
    PrimeStaking: XDC 위임 스테이킹 + Voter Delegation 보상
    """
    try:
        xdc_balance   = await _get_xdc_balance(wallet_address, rpc_url)
        xdc_price     = await _get_xdc_price_usd()
        staking_info  = await _get_primestaking_position(wallet_address)

        staking = []
        if staking_info["delegated_amount"] > 0:
            staking.append(StakingPosition(
                protocol="PrimeStaking",
                asset="XDC",
                staked_amount=staking_info["delegated_amount"],
                current_apy=staking_info["apy"],
                rewards_earned=staking_info["pending_rewards"],
                unlock_date=staking_info.get("epoch_end")
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
            fetch_error=f"XDC 조회 실패: {e}"
        )


def _web3_get_balance(rpc_url: str, address: str) -> float:
    """XDC는 EVM 호환. `xdc...` 주소는 `0x...` 로 변환하여 조회."""
    from web3 import Web3
    if address.startswith("xdc"):
        address = "0x" + address[3:]
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return float(Web3.from_wei(balance_wei, "ether"))


async def _get_xdc_balance(address: str, rpc_url: str) -> float:
    if is_placeholder_wallet(address) or not rpc_url:
        return 8500.0
    try:
        return await run_blocking(_web3_get_balance, rpc_url, address)
    except Exception as e:
        log.warning(f"XDC RPC 실패, mock 폴백: {type(e).__name__}: {e}")
        return 8500.0


async def _get_xdc_price_usd() -> float:
    price = await fetch_price_usd("XDC")
    return price if price is not None else 0.0432


async def _get_primestaking_position(wallet: str) -> dict:
    # PrimeStaking API: https://primestaking.net/api/delegator/{wallet}
    # 현재는 mock. placeholder 주소든 실제 주소든 동일 값 반환.
    return {
        "delegated_amount": 50000.0,
        "apy": 12.5,
        "pending_rewards": 625.0,
        "epoch_end": "2025-06-30",
        "masternode": "xdc4a..."
    }
