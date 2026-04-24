// ── 공통 도구 (모든 에이전트가 사용) ──────────────────────────────

class AskHumanCall {
  tool "ask_human"
  level "info" | "warning" | "critical"
  question string @description("사람에게 묻는 질문")
  context string? @description("추가 컨텍스트 (선택)")
  reason string
}

class DoneCall {
  tool "done"
  summary string @description("작업 완료 요약")
  reason string
}

// ── Monitor Agent 도구 ──────────────────────────────────────────────

class FetchPortfoliosCall {
  tool "fetch_portfolios"
  reason string
}

class DetectAlertsCall {
  tool "detect_alerts"
  reason string
}

class FetchPriceHistoryCall {
  tool "fetch_price_history"
  asset "FLR" | "XDC" | "XRP" @description("조회할 자산 심볼")
  days int @description("조회할 일수 (기본 7일)")
  reason string
}

// ── News Agent 도구 ─────────────────────────────────────────────────

class FetchNewsCall {
  tool "fetch_news"
  chain "Flare" | "XDC" | "all" @description("조회할 체인")
  reason string
}

class AnalyzeSentimentCall {
  tool "analyze_sentiment"
  reason string
}

// ── Trader Agent 도구 ───────────────────────────────────────────────

class CheckLiquidityCall {
  tool "check_liquidity"
  pair string @description("거래쌍 (예: FXRP/FLR)")
  reason string
}

class SimulateSwapCall {
  tool "simulate_swap"
  from_asset string @description("소스 자산 심볼 (예: FLR)")
  to_asset string @description("대상 자산 심볼 (예: FXRP)")
  amount float @description("스왑 수량")
  reason string
}

class GetOptimalRouteCall {
  tool "get_optimal_route"
  reason string
}

// ── Rebalancer Agent 도구 ───────────────────────────────────────────

class GetCurrentAllocationCall {
  tool "get_current_allocation"
  reason string
}

class GetTargetAllocationCall {
  tool "get_target_allocation"
  strategy "balanced" | "aggressive" | "conservative" @description("리밸런싱 전략")
  reason string
}

class CalculateRebalanceCall {
  tool "calculate_rebalance"
  reason string
}

// ── Tax Agent 도구 ──────────────────────────────────────────────────

class GetTransactionHistoryCall {
  tool "get_transaction_history"
  period string @description("조회 기간 (예: 2025)")
  reason string
}

class CalculateTaxCall {
  tool "calculate_tax"
  jurisdiction string @description("세금 관할권 코드 (예: KR)")
  reason string
}

class GetTaxOptimizationTipsCall {
  tool "get_tax_optimization_tips"
  reason string
}

// ── Controller Agent 도구 ───────────────────────────────────────────

class DelegateCall {
  tool "delegate"
  agent "monitor" | "news" | "trader" | "rebalancer" | "tax" @description("위임할 Sub-Agent 이름")
  task string @description("Sub-Agent에게 전달할 구체적 작업 지시")
  reason string
}

class GenerateReportCall {
  tool "generate_report"
  type "daily" | "weekly" | "alert" @description("리포트 종류")
  reason string
}

class SendTelegramCall {
  tool "send_telegram_alert"
  message string @description("전송할 메시지 내용")
  level "info" | "warning" | "critical" @description("알림 수준")
  reason string
}

class SendToNotionCall {
  tool "send_to_notion"
  report_id string @description("저장할 리포트 ID")
  reason string
}

// ── 에이전트별 union 타입 ────────────────────────────────────────────

type MonitorToolCall = FetchPortfoliosCall | DetectAlertsCall | FetchPriceHistoryCall | AskHumanCall | DoneCall

type NewsToolCall = FetchNewsCall | AnalyzeSentimentCall | AskHumanCall | DoneCall

type TraderToolCall = CheckLiquidityCall | SimulateSwapCall | GetOptimalRouteCall | AskHumanCall | DoneCall

type RebalancerToolCall = GetCurrentAllocationCall | GetTargetAllocationCall | CalculateRebalanceCall | AskHumanCall | DoneCall

type TaxToolCall = GetTransactionHistoryCall | CalculateTaxCall | GetTaxOptimizationTipsCall | AskHumanCall | DoneCall

type ControllerToolCall = DelegateCall | GenerateReportCall | SendTelegramCall | SendToNotionCall | AskHumanCall | DoneCall
