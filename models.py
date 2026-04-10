# models.py — 공유 데이터 모델 (dataclass)
# 모든 fetcher는 이 구조를 반환해야 함

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum


class AlertLevel(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class StakingPosition:
    """스테이킹 포지션"""
    protocol: str           # "Firelight", "PrimeStaking", "earnXRP" 등
    asset: str              # "stXRP", "XDC", "XRP"
    staked_amount: float
    current_apy: float      # %
    rewards_earned: float   # 누적 보상
    unlock_date: Optional[str] = None   # 락업 종료일


@dataclass
class DeFiPosition:
    """DeFi 포지션 (LP, 볼트 등)"""
    protocol: str           # "SparkDEX", "BlazeSwap" 등
    position_type: str      # "LP", "Vault", "Lending"
    assets: list[str]       # ["FXRP", "FLR"]
    value_usd: float
    pnl_usd: float          # 비영구 손실 포함
    share_pct: float        # 풀 내 비중 %


@dataclass
class ChainPortfolio:
    """체인별 포트폴리오 스냅샷"""
    chain: str              # "Flare", "XDC", "XRP"
    wallet_address: str
    native_balance: float   # FLR / XDC / XRP
    native_price_usd: float
    total_value_usd: float
    staking_positions: list[StakingPosition] = field(default_factory=list)
    defi_positions: list[DeFiPosition] = field(default_factory=list)
    fetch_error: Optional[str] = None  # 에러 발생 시


@dataclass
class PortfolioSnapshot:
    """전체 포트폴리오 통합 스냅샷 (Factor 5: 실행상태 + 비즈니스상태 통합)"""
    timestamp: datetime
    chains: list[ChainPortfolio]
    total_value_usd: float
    total_staking_rewards_usd: float
    alerts: list[dict] = field(default_factory=list)  # {"level": AlertLevel, "message": str}

    def to_context_summary(self) -> str:
        """LLM 컨텍스트에 넣을 요약 문자열 생성"""
        lines = [
            f"[포트폴리오 스냅샷 — {self.timestamp.strftime('%Y-%m-%d %H:%M')}]",
            f"총 자산: ${self.total_value_usd:,.2f}",
            f"스테이킹 누적 보상: ${self.total_staking_rewards_usd:,.2f}",
            ""
        ]
        for chain in self.chains:
            if chain.fetch_error:
                lines.append(f"⚠️ {chain.chain} 조회 실패: {chain.fetch_error}")
                continue
            lines.append(f"── {chain.chain} ({chain.wallet_address[:8]}...)")
            lines.append(f"   잔액: {chain.native_balance:.4f} @ ${chain.native_price_usd:.4f} = ${chain.total_value_usd:,.2f}")
            for sp in chain.staking_positions:
                lines.append(f"   스테이킹 [{sp.protocol}] {sp.staked_amount:.4f} {sp.asset} APY={sp.current_apy:.1f}%")
            for dp in chain.defi_positions:
                lines.append(f"   DeFi [{dp.protocol}/{dp.position_type}] ${dp.value_usd:,.2f} PnL=${dp.pnl_usd:+,.2f}")

        if self.alerts:
            lines.append("")
            lines.append("🚨 알림:")
            for alert in self.alerts:
                lines.append(f"   [{str(alert['level']).upper()}] {alert['message']}")

        return "\n".join(lines)
