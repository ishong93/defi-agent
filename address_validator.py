# address_validator.py — 네트워크 호출 전 1회 주소 검증.
#
# 목적: 잘못된/placeholder 주소가 fetcher 로 흘러들어 "silent 0 잔액" 으로
# 둔갑하는 것을 막는다. main.collect_snapshot 진입 직전에 호출한다.

from __future__ import annotations
import re

try:
    from web3 import Web3
    _WEB3_OK = True
except ImportError:
    _WEB3_OK = False


class InvalidWalletAddress(ValueError):
    """설정된 지갑 주소가 형식 검증을 통과하지 못함."""


# placeholder 기본값들 (명시적으로 거부)
_PLACEHOLDERS = {
    "0xyour_flare_wallet",
    "0xyour_xdc_wallet",
    "0xyour_xrpl_wallet",
    "your_wallet",
}

_EVM_HEX = re.compile(r"^0x[0-9a-fA-F]{40}$")
# XRPL classic address: 'r' + base58 (약 25~35자)
_XRPL_CLASSIC = re.compile(r"^r[1-9A-HJ-NP-Za-km-z]{24,34}$")


def validate_wallets(wallets: dict[str, str]) -> dict[str, str]:
    """
    {chain_key: address} 를 검증해 정규화된 dict 를 반환.
    - FLR: EVM 0x-form (체크섬 적용)
    - XDC: xdc... 또는 0x...  (둘 다 0x-form 으로 정규화, 체크섬 적용)
    - XRP: XRPL classic address (r...)
    실패 시 InvalidWalletAddress 예외.
    """
    out: dict[str, str] = {}
    for chain, raw in wallets.items():
        if raw is None or not str(raw).strip():
            raise InvalidWalletAddress(f"{chain}: 주소가 비어있음")
        addr = str(raw).strip()
        if addr.lower() in _PLACEHOLDERS:
            raise InvalidWalletAddress(
                f"{chain}: placeholder 주소 '{addr}' 는 사용할 수 없습니다. "
                f"WALLET_{chain} 환경변수 또는 --wallet-* CLI 인자로 실제 주소를 넘겨주세요."
            )

        if chain in ("FLR", "XDC"):
            out[chain] = _normalize_evm(chain, addr)
        elif chain == "XRP":
            out[chain] = _normalize_xrpl(addr)
        else:
            raise InvalidWalletAddress(f"알 수 없는 체인 키: {chain}")
    return out


def _normalize_evm(chain: str, addr: str) -> str:
    """xdc / 0x 접두사를 0x-form 으로 정규화하고 체크섬 검증."""
    if chain == "XDC" and addr.lower().startswith("xdc"):
        addr = "0x" + addr[3:]
    if not _EVM_HEX.match(addr):
        raise InvalidWalletAddress(
            f"{chain}: EVM 주소 형식 아님 ({addr[:10]}...). "
            f"0x + 40 hex 문자여야 합니다."
        )
    if _WEB3_OK:
        try:
            return Web3.to_checksum_address(addr)
        except (ValueError, Exception) as e:
            raise InvalidWalletAddress(
                f"{chain}: 체크섬 검증 실패 ({addr}): {e}"
            )
    return addr


def _normalize_xrpl(addr: str) -> str:
    if not _XRPL_CLASSIC.match(addr):
        raise InvalidWalletAddress(
            f"XRP: XRPL classic address 형식 아님 ({addr[:10]}...). "
            f"'r' 로 시작하는 base58 주소여야 합니다."
        )
    return addr
