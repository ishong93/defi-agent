# data_fetchers/flare_fetcher.py — Factor 4: Tools are structured outputs
# Flare Network: FLR 잔액, FXRP, stXRP (Firelight Finance ERC-4626 Vault)

import asyncio
from datetime import datetime
from models import ChainPortfolio, StakingPosition, DeFiPosition

# 실제 환경에서는 web3.py + requests 사용
# from web3 import Web3
# import requests


# ── ABI 상수 (Firelight stXRP Vault, SparkDEX LP) ──────────────────
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
        # ── 1. FLR 네이티브 잔액 ──────────────────────────────────
        flr_balance = await _get_native_balance(wallet_address, rpc_url)
        flr_price   = await _get_flr_price_usd()

        # ── 2. stXRP (Firelight ERC-4626 Vault) ──────────────────
        stxrp_shares = await _get_erc4626_shares(wallet_address, STXRP_VAULT_ADDRESS, rpc_url)
        stxrp_assets = await _convert_shares_to_assets(stxrp_shares, STXRP_VAULT_ADDRESS, rpc_url)
        stxrp_apy    = await _get_firelight_apy()   # Firelight API

        staking = []
        if stxrp_shares > 0:
            staking.append(StakingPosition(
                protocol="Firelight Finance",
                asset="stXRP",
                staked_amount=stxrp_assets,
                current_apy=stxrp_apy,
                rewards_earned=stxrp_assets - stxrp_shares,  # 단순화
                unlock_date=None  # Firelight Phase 1: 락업 없음
            ))

        # ── 3. SparkDEX LP 포지션 (FXRP/FLR) ────────────────────
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
        # Factor 9: 에러를 구조화해서 반환 (예외를 삼키지 않음)
        return ChainPortfolio(
            chain="Flare", wallet_address=wallet_address,
            native_balance=0, native_price_usd=0, total_value_usd=0,
            fetch_error=f"Flare 조회 실패: {type(e).__name__}: {e}"
        )


# ── 내부 헬퍼들 ───────────────────────────────────────────────────

async def _get_native_balance(address: str, rpc_url: str) -> float:
    """FLR 네이티브 잔액 조회 (wei → FLR)"""
    # w3 = Web3(Web3.HTTPProvider(rpc_url))
    # balance_wei = w3.eth.get_balance(address)
    # return float(Web3.from_wei(balance_wei, 'ether'))
    return 1250.5  # Mock

async def _get_flr_price_usd() -> float:
    # CoinGecko API: https://api.coingecko.com/api/v3/simple/price?ids=flare-networks&vs_currencies=usd
    return 0.0185  # Mock

async def _get_xrp_price_usd() -> float:
    return 2.31  # Mock

async def _get_erc4626_shares(wallet: str, vault: str, rpc_url: str) -> float:
    # w3.eth.contract(address=vault, abi=ERC4626_ABI_MINIMAL).functions.balanceOf(wallet).call()
    return 500.0  # Mock: 500 stXRP shares

async def _convert_shares_to_assets(shares: float, vault: str, rpc_url: str) -> float:
    # ERC-4626 convertToAssets: shares * (totalAssets / totalSupply)
    return shares * 1.043  # Mock: 4.3% 수익 반영

async def _get_firelight_apy() -> float:
    # Firelight Finance API 또는 온체인 이벤트 파싱
    return 8.7  # Mock: 8.7% APY

async def _get_sparkdex_lp_positions(wallet: str, rpc_url: str) -> list[DeFiPosition]:
    # SparkDEX V2 SubGraph 쿼리 또는 LP 토큰 balanceOf
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
