# tests/test_data_fetchers.py — data_fetchers 단위 테스트 (외부 API mock)

import asyncio
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from models import ChainPortfolio, StakingPosition, DeFiPosition


# ── price_feed ────────────────────────────────────────────────────────

class TestPriceFeed:
    def test_get_token_price_usd_valid(self):
        from data_fetchers.price_feed import get_token_price_usd, _cache
        _cache.clear()

        mock_response = MagicMock()
        mock_response.json.return_value = {"flare-networks": {"usd": 0.0185}}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            price = asyncio.get_event_loop().run_until_complete(get_token_price_usd("FLR"))
            assert price == pytest.approx(0.0185)

    def test_cache_hit(self):
        import time
        from data_fetchers.price_feed import get_token_price_usd, _cache
        _cache["XRP"] = (2.31, time.monotonic() + 60)

        price = asyncio.get_event_loop().run_until_complete(get_token_price_usd("XRP"))
        assert price == pytest.approx(2.31)

    def test_unsupported_token_raises(self):
        from data_fetchers.price_feed import get_token_price_usd
        with pytest.raises(ValueError, match="지원하지 않는 토큰"):
            asyncio.get_event_loop().run_until_complete(get_token_price_usd("UNKNOWN"))


# ── flare_fetcher ─────────────────────────────────────────────────────

class TestFlareFetcher:
    def _make_mock_prices(self, flr=0.02, xrp=2.30):
        import time
        from data_fetchers import price_feed
        price_feed._cache["FLR"] = (flr, time.monotonic() + 60)
        price_feed._cache["XRP"] = (xrp, time.monotonic() + 60)

    def test_fetch_flare_portfolio_success(self):
        from data_fetchers.flare_fetcher import fetch_flare_portfolio
        self._make_mock_prices()

        with patch("data_fetchers.flare_fetcher._get_native_balance", new=AsyncMock(return_value=1000.0)), \
             patch("data_fetchers.flare_fetcher._get_stxrp_position",
                   new=AsyncMock(return_value={"shares": 500.0, "assets": 521.5, "apy": 8.7})), \
             patch("data_fetchers.flare_fetcher._get_sparkdex_lp_positions",
                   new=AsyncMock(return_value=[DeFiPosition(
                       protocol="SparkDEX", position_type="LP",
                       assets=["FXRP", "FLR"], value_usd=340.0, pnl_usd=12.0, share_pct=0.002
                   )])):

            result = asyncio.get_event_loop().run_until_complete(
                fetch_flare_portfolio("0xTestWallet", "https://rpc.flare.network")
            )

        assert isinstance(result, ChainPortfolio)
        assert result.chain == "Flare"
        assert result.fetch_error is None
        assert result.native_balance == 1000.0
        assert len(result.staking_positions) == 1
        assert result.staking_positions[0].protocol == "Firelight Finance"
        assert result.staking_positions[0].current_apy == pytest.approx(8.7)
        assert len(result.defi_positions) == 1

    def test_fetch_flare_portfolio_error_returns_graceful(self):
        from data_fetchers.flare_fetcher import fetch_flare_portfolio
        self._make_mock_prices()

        with patch("data_fetchers.flare_fetcher._get_native_balance",
                   new=AsyncMock(side_effect=ConnectionError("RPC 연결 실패"))):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_flare_portfolio("0xBadWallet", "https://bad-rpc")
            )

        assert result.chain == "Flare"
        assert result.fetch_error is not None
        assert "Flare 조회 실패" in result.fetch_error
        assert result.total_value_usd == 0

    def test_no_stxrp_position(self):
        from data_fetchers.flare_fetcher import fetch_flare_portfolio
        self._make_mock_prices()

        with patch("data_fetchers.flare_fetcher._get_native_balance", new=AsyncMock(return_value=500.0)), \
             patch("data_fetchers.flare_fetcher._get_stxrp_position",
                   new=AsyncMock(return_value={"shares": 0.0, "assets": 0.0, "apy": 0.0})), \
             patch("data_fetchers.flare_fetcher._get_sparkdex_lp_positions",
                   new=AsyncMock(return_value=[])):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_flare_portfolio("0xEmptyWallet", "https://rpc.flare.network")
            )

        assert result.staking_positions == []
        assert result.defi_positions == []


# ── xdc_fetcher ───────────────────────────────────────────────────────

class TestXdcFetcher:
    def _make_mock_xdc_price(self, price=0.043):
        import time
        from data_fetchers import price_feed
        price_feed._cache["XDC"] = (price, time.monotonic() + 60)

    def test_fetch_xdc_portfolio_with_staking(self):
        from data_fetchers.xdc_fetcher import fetch_xdc_portfolio
        self._make_mock_xdc_price()

        staking_data = {
            "delegated_amount": 50000.0, "apy": 12.5,
            "pending_rewards": 625.0, "epoch_end": "2025-06-30", "masternode": "xdc4a..."
        }

        with patch("data_fetchers.xdc_fetcher._get_xdc_balance", new=AsyncMock(return_value=8500.0)), \
             patch("data_fetchers.xdc_fetcher._get_primestaking_position", new=AsyncMock(return_value=staking_data)):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_xdc_portfolio("xdc1234abcd", "https://rpc.xinfin.network")
            )

        assert result.chain == "XDC"
        assert result.fetch_error is None
        assert len(result.staking_positions) == 1
        sp = result.staking_positions[0]
        assert sp.protocol == "PrimeStaking"
        assert sp.staked_amount == pytest.approx(50000.0)
        assert sp.current_apy == pytest.approx(12.5)

    def test_xdc_address_conversion(self):
        from data_fetchers.xdc_fetcher import _xdc_to_eth_address
        assert _xdc_to_eth_address("xdc1a2b3c") == "0x1a2b3c"
        assert _xdc_to_eth_address("0x1a2b3c") == "0x1a2b3c"

    def test_fetch_xdc_error_graceful(self):
        from data_fetchers.xdc_fetcher import fetch_xdc_portfolio
        self._make_mock_xdc_price()

        with patch("data_fetchers.xdc_fetcher._get_xdc_balance",
                   new=AsyncMock(side_effect=TimeoutError("RPC timeout"))):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_xdc_portfolio("xdcBadWallet", "https://bad-rpc")
            )

        assert result.fetch_error is not None
        assert result.total_value_usd == 0


# ── xrpl_fetcher ──────────────────────────────────────────────────────

class TestXrplFetcher:
    def _make_mock_xrp_price(self, price=2.30):
        import time
        from data_fetchers import price_feed
        price_feed._cache["XRP"] = (price, time.monotonic() + 60)

    def test_fetch_xrpl_portfolio_success(self):
        from data_fetchers.xrpl_fetcher import fetch_xrpl_portfolio
        self._make_mock_xrp_price()

        staking_data = {
            "staked_amount": 1000.0, "apy": 5.2,
            "rewards_earned": 52.0, "unlock_date": None
        }

        with patch("data_fetchers.xrpl_fetcher._get_xrp_balance", new=AsyncMock(return_value=5000.0)), \
             patch("data_fetchers.xrpl_fetcher._get_earnxrp_position", new=AsyncMock(return_value=staking_data)), \
             patch("data_fetchers.xrpl_fetcher._get_amm_positions", new=AsyncMock(return_value=[])):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_xrpl_portfolio("rXRPWallet123", "wss://xrplcluster.com")
            )

        assert result.chain == "XRP"
        assert result.fetch_error is None
        assert result.native_balance == pytest.approx(5000.0)
        assert len(result.staking_positions) == 1
        assert result.staking_positions[0].protocol == "earnXRP"
        assert result.total_value_usd == pytest.approx((5000.0 + 1000.0) * 2.30)

    def test_fetch_xrpl_no_staking(self):
        from data_fetchers.xrpl_fetcher import fetch_xrpl_portfolio
        self._make_mock_xrp_price()

        empty_staking = {"staked_amount": 0.0, "apy": 0.0, "rewards_earned": 0.0, "unlock_date": None}

        with patch("data_fetchers.xrpl_fetcher._get_xrp_balance", new=AsyncMock(return_value=100.0)), \
             patch("data_fetchers.xrpl_fetcher._get_earnxrp_position", new=AsyncMock(return_value=empty_staking)), \
             patch("data_fetchers.xrpl_fetcher._get_amm_positions", new=AsyncMock(return_value=[])):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_xrpl_portfolio("rEmptyWallet", "wss://xrplcluster.com")
            )

        assert result.staking_positions == []
        assert result.total_value_usd == pytest.approx(100.0 * 2.30)

    def test_fetch_xrpl_error_graceful(self):
        from data_fetchers.xrpl_fetcher import fetch_xrpl_portfolio
        self._make_mock_xrp_price()

        with patch("data_fetchers.xrpl_fetcher._get_xrp_balance",
                   new=AsyncMock(side_effect=ConnectionRefusedError("WS 연결 거부"))):
            result = asyncio.get_event_loop().run_until_complete(
                fetch_xrpl_portfolio("rBadWallet", "wss://bad-ws")
            )

        assert result.fetch_error is not None
        assert "XRPL 조회 실패" in result.fetch_error
