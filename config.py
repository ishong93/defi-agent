# config.py — 모든 설정값은 여기서만 관리

from dataclasses import dataclass, field
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass
class AlertThresholds:
    """알림 발송 임계값"""
    price_drop_pct: float = 10.0       # 가격 10% 이상 하락 시 경고
    yield_drop_pct: float = 2.0        # APY 2%p 이상 감소 시 경고
    portfolio_drop_pct: float = 15.0   # 전체 포트폴리오 15% 이상 하락 시 긴급
    staking_unstake_risk: float = 0.8  # 언스테이킹 위험 점수 0.8 이상 시 경고


@dataclass
class ChainConfig:
    """체인별 RPC 설정"""
    flare_rpc: str = field(default_factory=lambda: os.getenv(
        "FLARE_RPC_URL", "https://flare-api.flare.network/ext/C/rpc"))
    xdc_rpc: str = field(default_factory=lambda: os.getenv(
        "XDC_RPC_URL", "https://rpc.xinfin.network"))
    xrpl_ws: str = field(default_factory=lambda: os.getenv(
        "XRPL_WS", "wss://xrplcluster.com"))


@dataclass
class AgentConfig:
    """에이전트 동작 설정"""
    model: str = "claude-opus-4-5"
    max_steps: int = 15
    check_interval_minutes: int = 60   # 정기 모니터링 주기
    report_hour: int = 9               # 매일 오전 9시 일간 리포트
    language: str = "ko"               # 리포트 언어
    chains: ChainConfig = field(default_factory=ChainConfig)
    alerts: AlertThresholds = field(default_factory=AlertThresholds)

    # 출력 채널 (선택)
    telegram_token: Optional[str] = field(
        default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: Optional[str] = field(
        default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID"))
    notion_api_key: Optional[str] = field(
        default_factory=lambda: os.getenv("NOTION_API_KEY"))
    notion_db_id: Optional[str] = field(
        default_factory=lambda: os.getenv("NOTION_DB_ID"))


CONFIG = AgentConfig()
