# data_fetchers/flare_fetcher.py — Factor 4: Tools are structured outputs
# Flare Network: FLR 잔액, FXRP, stXRP (Firelight Finance ERC-4626 Vault)

import asyncio
import httpx
from models import ChainPortfolio, StakingPosition, DeFiPosition
from data_fetchers.price_feed import get_token_price_usd

try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    WEB3_AVAILABLE = True
except ImportError:
    WEB3_AVAILABLE = False


# ── 컨트랙트 주소 ────────────────────────────────────────────────────
STXRP_VAULT_ADDRESS = "0x1D80c49BbBCd1C0911346656B529DF9E5c2F783d"  # Firelight stXRP ERC-4626
SPARKDEX_SUBGRAPH   = "https://api.thegraph.com/subgraphs/name/sparkdex/sparkdex-v2"
FIRELIGHT_API       = "https://app.firelight.finance/api"

ERC4626_ABI = [
    {"name": "balanceOf",       "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "convertToAssets", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "shares", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "totalAssets",     "type": "function", "stateMutability": "view",
     "inputs": [],
     "outputs": [{"name": "", "type": "uint256"}]},
]


async def fetch_flare_portfolio(wallet_address: str, rpc_url: str) -> ChainPortfolio:
    """
    Flare Network 포트폴리오 조회.
    반환: ChainPortfolio (Factor 4 — 구조화된 출력)
    """
    try:
        flr_price, xrp_price = await asyncio.gather(
            get_token_price_usd("FLR"),
            get_token_price_usd("XRP"),
        )

        flr_balance, stxrp_data, lp_positions = await asyncio.gather(
            _get_native_balance(wallet_address, rpc_url),
            _get_stxrp_position(wallet_address, rpc_url),
            _get_sparkdex_lp_positions(wallet_address),
        )

        staking = []
        if stxrp_data["shares"] > 0:
            staking.append(StakingPosition(
                protocol="Firelight Finance",
                asset="stXRP",
                staked_amount=stxrp_data["assets"],
                current_apy=stxrp_data["apy"],
                rewards_earned=stxrp_data["assets"] - stxrp_data["shares"],
                unlock_date=None  # Firelight Phase 1: 락업 없음
            ))

        lp_value_usd = sum(p.value_usd for p in lp_positions)
        total_usd = (flr_balance * flr_price) + (stxrp_data["assets"] * xrp_price) + lp_value_usd

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


# ── 내부 헬퍼 ────────────────────────────────────────────────────────

async def _get_native_balance(address: str, rpc_url: str) -> float:
    """FLR 네이티브 잔액 조회 (wei → FLR). web3.py 사용, 미설치 시 RPC 직접 호출."""
    if WEB3_AVAILABLE:
        return await asyncio.get_event_loop().run_in_executor(
            None, _web3_get_balance, address, rpc_url
        )
    return await _rpc_get_balance(address, rpc_url)


def _web3_get_balance(address: str, rpc_url: str) -> float:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    balance_wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return float(Web3.from_wei(balance_wei, "ether"))


async def _rpc_get_balance(address: str, rpc_url: str) -> float:
    """web3.py 없을 때 JSON-RPC eth_getBalance 직접 호출."""
    payload = {"jsonrpc": "2.0", "method": "eth_getBalance",
               "params": [address, "latest"], "id": 1}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        hex_balance = resp.json()["result"]
        wei = int(hex_balance, 16)
        return wei / 10**18


async def _get_stxrp_position(wallet: str, rpc_url: str) -> dict:
    """Firelight stXRP ERC-4626 Vault 포지션: shares, assets, APY"""
    shares, apy = await asyncio.gather(
        _erc4626_balance_of(wallet, STXRP_VAULT_ADDRESS, rpc_url),
        _get_firelight_apy(),
    )
    if shares == 0:
        return {"shares": 0.0, "assets": 0.0, "apy": apy}

    assets = await _erc4626_convert_to_assets(shares, STXRP_VAULT_ADDRESS, rpc_url)
    return {"shares": shares, "assets": assets, "apy": apy}


async def _erc4626_balance_of(wallet: str, vault: str, rpc_url: str) -> float:
    """ERC-4626 balanceOf — web3.py 또는 eth_call 직접 호출"""
    if WEB3_AVAILABLE:
        return await asyncio.get_event_loop().run_in_executor(
            None, _web3_erc4626_balance_of, wallet, vault, rpc_url
        )
    # eth_call: balanceOf(address) selector = 0x70a08231
    data = "0x70a08231" + wallet.replace("0x", "").zfill(64)
    return await _eth_call_uint256(vault, data, rpc_url) / 10**18


def _web3_erc4626_balance_of(wallet: str, vault: str, rpc_url: str) -> float:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(vault), abi=ERC4626_ABI
    )
    shares_wei = contract.functions.balanceOf(Web3.to_checksum_address(wallet)).call()
    return shares_wei / 10**18


async def _erc4626_convert_to_assets(shares: float, vault: str, rpc_url: str) -> float:
    """ERC-4626 convertToAssets"""
    if WEB3_AVAILABLE:
        return await asyncio.get_event_loop().run_in_executor(
            None, _web3_convert_to_assets, shares, vault, rpc_url
        )
    shares_wei = int(shares * 10**18)
    # convertToAssets(uint256) selector = 0x07a2d13a
    data = "0x07a2d13a" + hex(shares_wei)[2:].zfill(64)
    return await _eth_call_uint256(vault, data, rpc_url) / 10**18


def _web3_convert_to_assets(shares: float, vault: str, rpc_url: str) -> float:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(vault), abi=ERC4626_ABI
    )
    shares_wei = int(shares * 10**18)
    assets_wei = contract.functions.convertToAssets(shares_wei).call()
    return assets_wei / 10**18


async def _eth_call_uint256(to: str, data: str, rpc_url: str) -> int:
    payload = {"jsonrpc": "2.0", "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"], "id": 1}
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(rpc_url, json=payload)
        resp.raise_for_status()
        result = resp.json().get("result", "0x0")
        return int(result, 16)


async def _get_firelight_apy() -> float:
    """Firelight Finance API에서 stXRP APY 조회"""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(f"{FIRELIGHT_API}/vault/stxrp/apy")
            resp.raise_for_status()
            return float(resp.json().get("apy", 0.0))
    except Exception:
        return 0.0


async def _get_sparkdex_lp_positions(wallet: str) -> list[DeFiPosition]:
    """SparkDEX SubGraph에서 LP 포지션 조회"""
    query = """
    query($wallet: String!) {
      liquidityPositions(where: { user: $wallet }) {
        pair { token0 { symbol } token1 { symbol } reserveUSD }
        liquidityTokenBalance
        pair { totalSupply }
      }
    }
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                SPARKDEX_SUBGRAPH,
                json={"query": query, "variables": {"wallet": wallet.lower()}},
            )
            resp.raise_for_status()
            positions = resp.json().get("data", {}).get("liquidityPositions", [])

        result = []
        for pos in positions:
            pair = pos["pair"]
            total_supply = float(pair.get("totalSupply") or 1)
            lp_balance = float(pos.get("liquidityTokenBalance") or 0)
            share_pct = lp_balance / total_supply if total_supply > 0 else 0
            reserve_usd = float(pair.get("reserveUSD") or 0)
            value_usd = reserve_usd * share_pct
            result.append(DeFiPosition(
                protocol="SparkDEX",
                position_type="LP",
                assets=[pair["token0"]["symbol"], pair["token1"]["symbol"]],
                value_usd=value_usd,
                pnl_usd=0.0,  # SubGraph에서 IL 계산 미제공
                share_pct=share_pct,
            ))
        return result
    except Exception:
        return []
