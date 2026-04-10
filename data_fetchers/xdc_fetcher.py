# data_fetchers/xdc_fetcher.py — XDC Network + PrimeStaking

from models import ChainPortfolio, StakingPosition


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


async def _get_xdc_balance(address: str, rpc_url: str) -> float:
    return 8500.0  # Mock

async def _get_xdc_price_usd() -> float:
    return 0.0432  # Mock

async def _get_primestaking_position(wallet: str) -> dict:
    # PrimeStaking API: https://primestaking.net/api/delegator/{wallet}
    return {
        "delegated_amount": 50000.0,
        "apy": 12.5,
        "pending_rewards": 625.0,
        "epoch_end": "2025-06-30",
        "masternode": "xdc4a..."
    }
