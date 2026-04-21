# tests/test_address_validator.py — 지갑 주소 검증 로직 회귀 테스트.

import pytest

from address_validator import InvalidWalletAddress, validate_wallets


# ── 정상 케이스 ─────────────────────────────────────────────

def test_valid_evm_flare_is_checksummed():
    # 소문자 입력 → 체크섬 포맷으로 정규화
    addr = "0x52908400098527886e0f7030069857d2e4169ee7"
    out = validate_wallets({"FLR": addr})
    assert out["FLR"].startswith("0x")
    assert len(out["FLR"]) == 42
    # web3 가 있으면 혼합 대소문자, 없으면 원본
    assert out["FLR"].lower() == addr


def test_valid_xdc_with_xdc_prefix_normalized_to_0x():
    addr = "xdc52908400098527886e0f7030069857d2e4169ee7"
    out = validate_wallets({"XDC": addr})
    assert out["XDC"].startswith("0x")
    assert out["XDC"].lower() == "0x52908400098527886e0f7030069857d2e4169ee7"


def test_valid_xdc_with_0x_prefix_accepted():
    addr = "0x52908400098527886e0f7030069857d2e4169ee7"
    out = validate_wallets({"XDC": addr})
    assert out["XDC"].lower() == addr


def test_valid_xrpl_classic_address():
    addr = "rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH"
    out = validate_wallets({"XRP": addr})
    assert out["XRP"] == addr


def test_multiple_chains_together():
    wallets = {
        "FLR": "0x52908400098527886e0f7030069857d2e4169ee7",
        "XDC": "xdc52908400098527886e0f7030069857d2e4169ee7",
        "XRP": "rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
    }
    out = validate_wallets(wallets)
    assert set(out.keys()) == {"FLR", "XDC", "XRP"}


# ── 거부 케이스 ─────────────────────────────────────────────

@pytest.mark.parametrize("placeholder", [
    "0xYOUR_FLARE_WALLET",
    "0xyour_xdc_wallet",
    "0xYOUR_XRPL_WALLET",
    "your_wallet",
])
def test_placeholder_addresses_rejected(placeholder):
    with pytest.raises(InvalidWalletAddress, match="placeholder"):
        validate_wallets({"FLR": placeholder})


def test_empty_address_rejected():
    with pytest.raises(InvalidWalletAddress, match="비어있음"):
        validate_wallets({"FLR": ""})


def test_none_address_rejected():
    with pytest.raises(InvalidWalletAddress, match="비어있음"):
        validate_wallets({"FLR": None})


def test_whitespace_only_address_rejected():
    with pytest.raises(InvalidWalletAddress, match="비어있음"):
        validate_wallets({"FLR": "   "})


def test_malformed_evm_too_short():
    with pytest.raises(InvalidWalletAddress, match="EVM 주소 형식"):
        validate_wallets({"FLR": "0xabc"})


def test_malformed_evm_not_hex():
    with pytest.raises(InvalidWalletAddress, match="EVM 주소 형식"):
        validate_wallets({"FLR": "0x" + "z" * 40})


def test_malformed_xrpl_no_r_prefix():
    with pytest.raises(InvalidWalletAddress, match="XRPL classic"):
        validate_wallets({"XRP": "xN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH"})


def test_malformed_xrpl_too_short():
    with pytest.raises(InvalidWalletAddress, match="XRPL classic"):
        validate_wallets({"XRP": "rShort"})


def test_unknown_chain_key_rejected():
    with pytest.raises(InvalidWalletAddress, match="알 수 없는 체인"):
        validate_wallets({"BTC": "0x52908400098527886e0f7030069857d2e4169ee7"})


# ── 체크섬 정규화 (web3 사용 가능 시) ──────────────────────

def test_evm_checksum_normalizes_lowercase_to_mixed_case():
    """소문자 입력이 web3 체크섬 포맷(혼합 대소문자) 으로 정규화된다."""
    try:
        from web3 import Web3  # noqa: F401
    except ImportError:
        pytest.skip("web3 미설치 — 체크섬 정규화 스킵")
    lower = "0x52908400098527886e0f7030069857d2e4169ee7"
    out = validate_wallets({"FLR": lower})
    # 정규화 후에는 대소문자가 섞여 있어야 한다 (순수 소문자 ≠ 체크섬 포맷)
    assert any(c.isupper() for c in out["FLR"])
    assert out["FLR"].lower() == lower
