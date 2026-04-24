# agents/registry.py — Sub-Agent 레지스트리
#
# Factor 10: 각 에이전트는 고유한 역할, 도구 세트, max_steps를 가진다.
# BAML 통합 후:
#   - 시스템 프롬프트는 baml_src/agents/*.baml 으로 이동.
#   - 이 파일은 도구 실행 함수(execution logic)만 관리한다.

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class SubAgentSpec:
    """Sub-Agent 명세 — 이름, 역할, 도구, 제한"""
    name: str
    description: str
    tools: dict[str, Callable] = field(default_factory=dict)
    max_steps: int = 8


def build_monitor_tools(snapshot) -> dict[str, Callable]:
    """Monitor Agent 전용 도구 세트"""
    def fetch_portfolios(params):
        return snapshot.to_context_summary()

    def detect_alerts(params):
        alerts = []
        for chain in snapshot.chains:
            if chain.fetch_error:
                alerts.append(f"[WARNING] {chain.chain} 데이터 조회 실패")
            for sp in chain.staking_positions:
                if sp.current_apy < 5.0:
                    alerts.append(f"[WARNING] {sp.protocol} {sp.asset} APY {sp.current_apy:.1f}%")
        if not alerts:
            alerts.append("[INFO] 이상 징후 없음")
        return "\n".join(alerts)

    def fetch_price_history(params):
        from shared_mocks import price_change_pct
        asset = params.get("asset", "FLR")
        prices, change = price_change_pct(asset)
        return f"{asset} 가격 추이: {prices}\n변동률: {change:+.2f}%"

    return {
        "fetch_portfolios": fetch_portfolios,
        "detect_alerts": detect_alerts,
        "fetch_price_history": fetch_price_history,
    }


def build_news_tools() -> dict[str, Callable]:
    """News Agent 전용 도구 세트"""
    def fetch_news(params):
        chain = params.get("chain", "all")
        mock_news = {
            "Flare": [
                {"title": "Firelight Finance stXRP Vault 출시", "sentiment": "positive", "impact": "high"},
                {"title": "SparkDEX V2 거래량 급증", "sentiment": "positive", "impact": "medium"},
            ],
            "XDC": [
                {"title": "XDC Network 기업 파트너십 확대", "sentiment": "positive", "impact": "medium"},
                {"title": "PrimeStaking APY 조정 예고", "sentiment": "neutral", "impact": "low"},
            ],
            "all": [
                {"title": "DeFi TVL 전체 상승 추세", "sentiment": "positive", "impact": "medium"},
            ]
        }
        news = mock_news.get(chain, mock_news["all"])
        lines = [f"- [{n['sentiment'].upper()}] {n['title']} (영향: {n['impact']})" for n in news]
        return f"[{chain} 뉴스]\n" + "\n".join(lines)

    def analyze_sentiment(params):
        return "전체 감성: 긍정적 (72%) | 부정적 (8%) | 중립 (20%)\n핵심 키워드: stXRP, SparkDEX, 파트너십"

    return {"fetch_news": fetch_news, "analyze_sentiment": analyze_sentiment}


def build_trader_tools(snapshot) -> dict[str, Callable]:
    """Trader Agent 전용 도구 세트"""
    def check_liquidity(params):
        pair = params.get("pair", "FXRP/FLR")
        return f"{pair} 유동성: $2.4M | 24h 거래량: $180K | 스프레드: 0.3%"

    def simulate_swap(params):
        from_asset = params.get("from", "FLR")
        to_asset   = params.get("to", "FXRP")
        amount     = params.get("amount", 100)
        slippage   = 0.5 if amount < 1000 else 1.2 if amount < 5000 else 2.8
        return (f"시뮬레이션: {amount} {from_asset} → {to_asset}\n"
                f"예상 슬리피지: {slippage}%\n예상 수수료: ${amount * 0.003:.2f}")

    def get_optimal_route(params):
        return "최적 경로: FLR → WFLR → FXRP (SparkDEX V2)\n대안: FLR → USDT → FXRP (BlazeSwap) — 0.2% 더 비쌈"

    return {
        "check_liquidity": check_liquidity,
        "simulate_swap": simulate_swap,
        "get_optimal_route": get_optimal_route,
    }


def build_rebalancer_tools(snapshot) -> dict[str, Callable]:
    """Rebalancer Agent 전용 도구 세트"""
    def get_current_allocation(params):
        total = snapshot.total_value_usd
        if total == 0:
            return "포트폴리오 비어있음"
        lines = [
            f"{chain.chain}: ${chain.total_value_usd:,.2f} ({chain.total_value_usd / total * 100:.1f}%)"
            for chain in snapshot.chains
        ]
        return "현재 배분:\n" + "\n".join(lines)

    def get_target_allocation(params):
        strategy = params.get("strategy", "balanced")
        targets = {
            "balanced":    "목표: Flare 40% / XDC 35% / XRP 25%",
            "aggressive":  "목표: Flare 50% / XDC 30% / XRP 20%",
            "conservative":"목표: Flare 30% / XDC 30% / XRP 40%",
        }
        return targets.get(strategy, targets["balanced"])

    def calculate_rebalance(params):
        return ("리밸런싱 계산:\n"
                "  Flare: +$500 매수 필요 (현재 38% → 목표 40%)\n"
                "  XDC: -$200 매도 필요 (현재 37% → 목표 35%)\n"
                "  XRP: -$300 매도 필요 (현재 25% → 목표 25% 유지)\n"
                "총 거래 비용 추정: $4.50")

    return {
        "get_current_allocation": get_current_allocation,
        "get_target_allocation":  get_target_allocation,
        "calculate_rebalance":    calculate_rebalance,
    }


def build_tax_tools(snapshot) -> dict[str, Callable]:
    """Tax Agent 전용 도구 세트"""
    def get_transaction_history(params):
        period = params.get("period", "2025")
        return (f"[{period} 거래 내역]\n"
                "  스테이킹 보상: 1,250 XDC ($54.00)\n"
                "  LP 수수료 수입: $12.50\n"
                "  스왑 거래: 3건 (총 $2,400)\n"
                "  실현 손익: +$85.30")

    def calculate_tax(params):
        jurisdiction = params.get("jurisdiction", "KR")
        return (f"[{jurisdiction} 세금 계산]\n"
                "  과세 대상 소득: $151.80\n"
                "  - 스테이킹 보상: $54.00 (기타소득)\n"
                "  - LP 수수료: $12.50 (기타소득)\n"
                "  - 실현 차익: $85.30 (양도소득)\n"
                "  추정 세액: $30.36 (20% 가정)")

    def get_tax_optimization_tips(params):
        return ("세금 최적화 권고:\n"
                "  1. 손실 실현: XRP -$15 미실현 손실 → 연말 전 실현 고려\n"
                "  2. 스테이킹 보상: 수령 시점 가격 기록 필수\n"
                "  3. 장기 보유 혜택: 1년 이상 보유 시 세율 우대 검토")

    return {
        "get_transaction_history":    get_transaction_history,
        "calculate_tax":              calculate_tax,
        "get_tax_optimization_tips":  get_tax_optimization_tips,
    }


def get_all_agent_specs(snapshot) -> dict[str, SubAgentSpec]:
    """모든 Sub-Agent 명세를 반환 (도구 실행 로직만 포함, 프롬프트는 BAML 관리)"""
    return {
        "monitor": SubAgentSpec(
            name="monitor",
            description="포트폴리오 모니터링 + 이상 징후 탐지",
            tools=build_monitor_tools(snapshot),
            max_steps=6,
        ),
        "news": SubAgentSpec(
            name="news",
            description="뉴스 수집 + 감성 분석",
            tools=build_news_tools(),
            max_steps=5,
        ),
        "trader": SubAgentSpec(
            name="trader",
            description="트레이딩 분석 + 스왑 시뮬레이션",
            tools=build_trader_tools(snapshot),
            max_steps=6,
        ),
        "rebalancer": SubAgentSpec(
            name="rebalancer",
            description="포트폴리오 리밸런싱 분석",
            tools=build_rebalancer_tools(snapshot),
            max_steps=5,
        ),
        "tax": SubAgentSpec(
            name="tax",
            description="세금 계산 + 최적화 권고",
            tools=build_tax_tools(snapshot),
            max_steps=5,
        ),
    }
