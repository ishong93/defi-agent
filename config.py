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
class ToolValidationConfig:
    """Factor 4: 도구 호출 검증 설정 (도구 호출 = 제안)"""
    max_transfer_usd: float = 10000.0       # 단일 전송 최대 금액 ($)
    max_slippage_pct: float = 3.0           # 스왑 최대 슬리피지 (%)
    require_human_above_usd: float = 5000.0 # 이 금액 이상이면 사람 확인 필수
    dry_run_default: bool = False            # 기본 드라이런 모드


@dataclass
class ErrorHandlingConfig:
    """Factor 9: 에러 처리 설정"""
    max_consecutive_errors: int = 3         # 연속 에러 N회 시 에스컬레이션
    max_error_msg_len: int = 200            # 에러 메시지 최대 길이
    escalate_to_human: bool = True          # 연속 에러 시 사람에게 에스컬레이션


@dataclass
class ContextConfig:
    """Factor 3/10: 컨텍스트 관리 설정"""
    max_context_messages: int = 20          # 컨텍스트 최대 메시지 수
    # Phase 5: "native" 가 기본 — Anthropic content-block + system= 파라미터 사용 (Role Hacking 제거).
    # "xml" | "plain" | "single" 은 레거시 JSON-in-text 경로 (ScriptedLLM 테스트 호환).
    context_format: str = "native"
    step_warning_threshold: int = 10        # Factor 10: 이 스텝 수 이후 경고
    snapshot_stale_minutes: int = 30        # Factor 6: 스냅샷 신선도 임계값 (분)


@dataclass
class AgentConfig:
    """에이전트 동작 설정"""
    model: str = "claude-sonnet-4-20250514"
    max_steps: int = 15
    check_interval_minutes: int = 60   # 정기 모니터링 주기
    report_hour: int = 9               # 매일 오전 9시 일간 리포트
    language: str = "ko"               # 리포트 언어
    chains: ChainConfig = field(default_factory=ChainConfig)
    alerts: AlertThresholds = field(default_factory=AlertThresholds)
    tool_validation: ToolValidationConfig = field(default_factory=ToolValidationConfig)
    error_handling: ErrorHandlingConfig = field(default_factory=ErrorHandlingConfig)
    context: ContextConfig = field(default_factory=ContextConfig)

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
