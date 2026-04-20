# data_fetchers/flare_fetcher.py — Factor 4: Tools are structured outputs
# Flare Network: FLR 잔액, FXRP, stXRP (Firelight Finance ERC-4626 Vault)
#
# 실제 지갑 주소면 web3.py / CoinGecko 호출, placeholder거나 실패 시 mock 폴백.

from models import ChainPortfolio, StakingPosition, DeFiPosition
from ._helpers import is_placeholder_wallet, run_blocking, log
from .price_provider import fetch_price_usd


STXRP_VAULT_ADDRESS = "0x..."   # Firelight stXRP ERC-4626 Vault
SPARKDEX_ROUTER    = "0x..."   # SparkDEX V2 Router
FXRP_ADDRESS       = "0x..."   # FXRP 토큰

ERC4626_ABI_MINIMAL = [
    {"name": "balanceOf",      "inputs": [{"type": "address"}], "outputs": [{"type": "uint256"}]},
    {"name": "convertToAssets","inputs": [{"type": "uint256"}], "outputs": [{"type": "uint256"}]},
    {"name": "totalAssets",    "inputs": [],                    "outputs": [{"type": "uint256"}]},
]


async def fetch_flare_portfolio(wallet_address: str, rpc_url: str) -> ChainPortfolio:
    """
    Flare Network 포트폴리오 조회.
    반환: ChainPortfolio (Factor 4 — 구조화된 출력)
    """
    try:
        flr_balance = await _get_native_balance(wallet_address, rpc_url)
        flr_price   = await _get_flr_price_usd()

        stxrp_shares = await _get_erc4626_shares(wallet_address, STXRP_VAULT_ADDRESS, rpc_url)
        stxrp_assets = await _convert_shares_to_assets(stxrp_shares, STXRP_VAULT_ADDRESS, rpc_url)
        stxrp_apy    = await _get_firelight_apy()

        staking = []
        if stxrp_shares > 0:
            staking.append(StakingPosition(
                protocol="Firelight Finance",
                asset="stXRP",
                staked_amount=stxrp_assets,
                current_apy=stxrp_apy,
                rewards_earned=stxrp_assets - stxrp_shares,
                unlock_date=None
            ))

        lp_positions = await _get_sparkdex_lp_positions(wallet_address, rpc_url)

        total_usd = (flr_balance * flr_price) + sum(
            p.value_usd for p in lp_positions
        ) + (stxrp_assets * await _get_xrp_price_usd())

        return ChainPortfolio(
            chain="Flare",
            wallet_address=wallet_address,
            native_balance=flr_balance,
            native_price_usd=flr_price,
            total_value_usd=total_usd,
            staking_positions=staking,
            defi_positions=lp_positions,
        )

    except Exception as e:
        return ChainPortfolio(
            chain="Flare", wallet_address=wallet_address,
            native_balance=0, native_price_usd=0, total_value_usd=0,
            fetch_error=f"Flare 조회 실패: {type(e).__name__}: {e}"
        )


# ── 내부 헬퍼들 ───────────────────────────────────────────────────

def _web3_get_balance(rpc_url: str, address: str) -> float:
    """동기 web3 호출. run_blocking으로 감싸서 호출."""
    from web3 import Web3
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return float(Web3.from_wei(balance_wei, "ether"))


async def _get_native_balance(address: str, rpc_url: str) -> float:
    """FLR 네이티브 잔액. placeholder면 mock, 실제 주소면 RPC (실패 시 mock)."""
    if is_placeholder_wallet(address) or not rpc_url:
        return 1250.5
    try:
        return await run_blocking(_web3_get_balance, rpc_url, address)
    except Exception as e:
        log.warning(f"Flare RPC 실패, mock 폴백: {type(e).__name__}: {e}")
        return 1250.5


async def _get_flr_price_usd() -> float:
    price = await fetch_price_usd("FLR")
    return price if price is not None else 0.0185


async def _get_xrp_price_usd() -> float:
    price = await fetch_price_usd("XRP")
    return price if price is not None else 2.31


async def _get_erc4626_shares(wallet: str, vault: str, rpc_url: str) -> float:
    # 실 환경에서 vault 주소가 확정되면 web3 contract 호출로 교체.
    return 500.0


async def _convert_shares_to_assets(shares: float, vault: str, rpc_url: str) -> float:
    return shares * 1.043


async def _get_firelight_apy() -> float:
    return 8.7


async def _get_sparkdex_lp_positions(wallet: str, rpc_url: str) -> list[DeFiPosition]:
    return [
        DeFiPosition(
            protocol="SparkDEX",
            position_type="LP",
            assets=["FXRP", "FLR"],
            value_usd=340.20,
            pnl_usd=12.50,
            share_pct=0.0023
        )
    ]
