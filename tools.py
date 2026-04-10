# tools.py — Factor 4: Tools are Structured Outputs
# 모든 툴 정의, 파싱, 실행 디스패치는 이 파일에서만

import json
from typing import Callable
from models import PortfolioSnapshot


def parse_tool_call(llm_output: str) -> dict:
    """LLM 출력에서 툴 호출 파싱"""
    try:
        text = llm_output.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception as e:
        # 파싱 실패 → ask_human으로 폴백
        return {
            "tool": "ask_human",
            "params": {"level": "info", "question": "LLM 응답 파싱 실패, 계속할까요?",
                       "context": llm_output[:150]},
            "reason": f"parse_error: {e}"
        }


# ── 툴 실행 레지스트리 ────────────────────────────────────────────

class ToolExecutor:
    """
    Factor 4: 툴은 단순히 구조화된 출력을 실행하는 함수들의 집합
    Factor 5: snapshot을 통해 실행 상태와 비즈니스 상태를 통합
    """

    def __init__(self, snapshot: PortfolioSnapshot, config):
        self.snapshot = snapshot
        self.config = config
        self._report_cache: dict = {}   # Factor 5: 생성된 리포트 상태 보관

    def dispatch(self, tool_call: dict) -> str:
        """툴 이름으로 실행 함수 디스패치"""
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
            raise ValueError(f"알 수 없는 툴: {tool}")

        return handlers[tool](params)

    def _fetch_all_portfolios(self, params: dict) -> str:
        """이미 수집된 스냅샷 반환 (사전 수집 패턴 — Factor 13)"""
        return self.snapshot.to_context_summary()

    def _fetch_price_history(self, params: dict) -> str:
        asset = params.get("asset", "FLR")
        days = params.get("days", 7)
        # 실제: CoinGecko API 호출
        mock_data = {
            "FLR": [0.0192, 0.0188, 0.0185, 0.0179, 0.0183, 0.0186, 0.0185],
            "XDC": [0.0445, 0.0441, 0.0438, 0.0432, 0.0435, 0.0433, 0.0432],
            "XRP": [2.35, 2.28, 2.25, 2.19, 2.22, 2.28, 2.31],
        }
        prices = mock_data.get(asset, [])
        change = ((prices[-1] - prices[0]) / prices[0] * 100) if prices else 0
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
        thresholds = self.config.alerts

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
        # 실제로는 Claude API를 다시 호출해서 리포트 텍스트 생성
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
        # 실제: notion-client 라이브러리로 DB에 페이지 생성
        return f"Notion 저장 완료: {report_id}"

    def _send_telegram_alert(self, params: dict) -> str:
        message = params.get("message", "")
        level = params.get("level", "info")
        if not self.config.telegram_token:
            print(f"[TELEGRAM MOCK — {level.upper()}] {message}")
            return f"텔레그램 전송 완료 (mock)"
        # 실제: requests.post(f"https://api.telegram.org/bot{token}/sendMessage", ...)
        return f"텔레그램 알림 발송 완료 [{level}]"
