# tools.py — Factor 4: Tools are Structured Outputs
#
# Factor 4 원문 핵심:
#   "LLM이 선택한 도구 호출은 '제안(proposal)'이지 '명령'이 아니다."
#   코드가 최종 결정권을 갖는다 — 검증, 거부, 드라이런 모두 가능.
#
# BAML 통합 후:
#   parse_tool_call() 제거 — BAML이 타입 안전한 파싱을 담당.
#   validate_tool_call()은 baml_bridge.baml_result_to_dict()로
#   변환된 dict를 받아 기존과 동일한 비즈니스 검증을 수행한다.

import json
from dataclasses import dataclass
from typing import Callable, Optional
from models import PortfolioSnapshot
from config import AgentConfig


# ── 검증 결과 타입 ───────────────────────────────────────────────

@dataclass
class ValidationResult:
    """도구 호출 검증 결과 (Factor 4: 도구 호출 = 제안)"""
    approved: bool
    reject_reason: Optional[str] = None
    requires_human: bool = False
    human_question: Optional[str] = None


# ── 검증 (Factor 4: Selection → Validation → Execution) ─────────

def validate_tool_call(tool_call: dict, config: AgentConfig) -> ValidationResult:
    """
    Factor 4: 도구 호출을 실행 전에 검증.
    LLM의 제안을 코드가 검토하는 단계.

    검증 항목:
    - 금액 한도 (max_transfer_usd)
    - 슬리피지 위험 (max_slippage_pct)
    - 사람 확인 필요 여부 (require_human_above_usd)
    """
    tool = tool_call.get("tool", "")
    params = tool_call.get("params", {})
    validation = config.tool_validation

    # done, ask_human은 항상 허용
    if tool in ("done", "ask_human"):
        return ValidationResult(approved=True)

    # 텔레그램 알림: critical 레벨은 사람 확인 필수
    if tool == "send_telegram_alert":
        level = params.get("level", "info")
        if level == "critical":
            return ValidationResult(
                approved=False,
                requires_human=True,
                human_question=f"Critical 알림 발송 승인이 필요합니다: {params.get('message', '')[:100]}"
            )

    # 금액이 포함된 도구: 한도 검증
    amount_usd = params.get("amount_usd", 0)
    if amount_usd > 0:
        if amount_usd > validation.max_transfer_usd:
            return ValidationResult(
                approved=False,
                reject_reason=f"금액 한도 초과: ${amount_usd:,.0f} > 최대 ${validation.max_transfer_usd:,.0f}"
            )
        if amount_usd > validation.require_human_above_usd:
            return ValidationResult(
                approved=False,
                requires_human=True,
                human_question=f"${amount_usd:,.0f} 규모 작업 승인이 필요합니다."
            )

    # 슬리피지 검증
    slippage = params.get("slippage_pct", 0)
    if slippage > validation.max_slippage_pct:
        return ValidationResult(
            approved=False,
            reject_reason=f"슬리피지 위험: {slippage}% > 최대 {validation.max_slippage_pct}%"
        )

    return ValidationResult(approved=True)


# ── 도구 실행 레지스트리 ─────────────────────────────────────────

class ToolExecutor:
    """
    Factor 4: 도구는 구조화된 출력을 실행하는 함수들의 집합.
    Factor 5: snapshot을 통해 실행 상태와 비즈니스 상태를 통합.

    핵심 흐름:
      LLM 제안 → validate_tool_call() → 승인 시 dispatch() → 결과 반환
    """

    def __init__(self, snapshot: PortfolioSnapshot, config: AgentConfig):
        self.snapshot = snapshot
        self.config = config
        self._report_cache: dict = {}

    def dispatch(self, tool_call: dict) -> str:
        """도구 이름으로 실행 함수 디스패치"""
        tool = tool_call.get("tool")
        params = tool_call.get("params", {})

        handlers: dict[str, Callable] = {
            "fetch_all_portfolios": self._fetch_all_portfolios,
            "fetch_price_history":  self._fetch_price_history,
            "fetch_defi_yields":    self._fetch_defi_yields,
            "analyze_portfolio":    self._analyze_portfolio,
            "detect_alerts":        self._detect_alerts,
            "generate_report":      self._generate_report,
            "send_to_notion":       self._send_to_notion,
            "send_telegram_alert":  self._send_telegram_alert,
        }

        if tool not in handlers:
            raise ValueError(f"알 수 없는 도구: {tool}")

        return handlers[tool](params)

    def _fetch_all_portfolios(self, params: dict) -> str:
        """이미 수집된 스냅샷 반환 (루프 시작 전 사전 수집 패턴)"""
        return self.snapshot.to_context_summary()

    def _fetch_price_history(self, params: dict) -> str:
        from shared_mocks import price_change_pct
        asset = params.get("asset", "FLR")
        days = params.get("days", 7)
        # 실제: CoinGecko API 호출
        prices, change = price_change_pct(asset)
        return f"{asset} {days}일 가격 추이: {prices}\n변동률: {change:+.2f}%"

    def _fetch_defi_yields(self, params: dict) -> str:
        chain = params.get("chain", "Flare")
        yields_data = {
            "Flare": "stXRP(Firelight): 8.7% APY | FXRP/FLR LP(SparkDEX): 24.3% APR | BlazeSwap FLR: 18.1% APR",
            "XDC":  "XDC 위임(PrimeStaking): 12.5% APY | XDC/USDT LP: 15.2% APR",
            "XRP":  "earnXRP: 6.2% APY | AMM XRPL: 8.8% APR",
        }
        return yields_data.get(chain, f"{chain} 수익률 데이터 없음")

    def _analyze_portfolio(self, params: dict) -> str:
        focus = params.get("focus", "yield")
        total = self.snapshot.total_value_usd
        staking_ratio = self.snapshot.total_staking_rewards_usd / total * 100 if total > 0 else 0

        analyses = {
            "yield":     f"총 스테이킹 비중: {staking_ratio:.1f}% | 평균 APY 추정: 10.8% | 연간 예상 수익: ${total * 0.108:,.0f}",
            "risk":      f"체인 집중도: Flare {30:.0f}% / XDC {50:.0f}% / XRP {20:.0f}% | 스테이블 비중: 0%",
            "rebalance": f"현재 배분 대비 목표 배분 차이: Flare +5% / XDC -3% / XRP -2% | 리밸런싱 권고: 소량 조정",
        }
        return analyses.get(focus, f"분석 유형 {focus} 없음")

    def _detect_alerts(self, params: dict) -> str:
        alerts = []
        for chain in self.snapshot.chains:
            if chain.fetch_error:
                alerts.append(f"[WARNING] {chain.chain} 데이터 조회 실패")

            for sp in chain.staking_positions:
                if sp.current_apy < 5.0:
                    alerts.append(f"[WARNING] {sp.protocol} {sp.asset} APY {sp.current_apy:.1f}% — 낮은 수익률")

        if not alerts:
            alerts.append("[INFO] 이상 징후 없음")

        self.snapshot.alerts = [{"level": a.split("]")[0][1:].lower(), "message": a} for a in alerts]
        return "\n".join(alerts)

    def _generate_report(self, params: dict) -> str:
        report_type = params.get("type", "daily")
        report_id = f"{report_type}_{self.snapshot.timestamp.strftime('%Y%m%d_%H%M')}"
        self._report_cache[report_id] = {
            "type": report_type,
            "snapshot": self.snapshot,
            "generated_at": self.snapshot.timestamp.isoformat()
        }
        return f"리포트 생성 완료 (ID: {report_id})\n총 자산: ${self.snapshot.total_value_usd:,.2f}"

    def _send_to_notion(self, params: dict) -> str:
        report_id = params.get("report_id", "")
        if not self.config.notion_api_key:
            return "Notion API 키 미설정 — 로컬 저장으로 대체"
        return f"Notion 저장 완료: {report_id}"

    def _send_telegram_alert(self, params: dict) -> str:
        message = params.get("message", "")
        level = params.get("level", "info")
        if not self.config.telegram_token:
            print(f"[TELEGRAM MOCK — {level.upper()}] {message}")
            return f"텔레그램 전송 완료 (mock)"
        return f"텔레그램 알림 발송 완료 [{level}]"
