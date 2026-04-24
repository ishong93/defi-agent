# baml_bridge.py — BAML 결과와 이벤트 시스템 간 브리지
#
# 역할:
#   1. 이벤트 목록 → BAML 컨텍스트 문자열 변환 (events_to_history_str)
#   2. BAML 결과 → validate_tool_call 호환 dict 변환 (baml_result_to_dict)
#   3. 에이전트 이름 → BAML 함수 라우팅 (_call_baml_agent)

from typing import Union
from baml_client.baml_client import b
from baml_client.baml_client.types import (
    # 공통
    AskHumanCall, DoneCall,
    # Monitor
    FetchPortfoliosCall, DetectAlertsCall, FetchPriceHistoryCall,
    # News
    FetchNewsCall, AnalyzeSentimentCall,
    # Trader
    CheckLiquidityCall, SimulateSwapCall, GetOptimalRouteCall,
    # Rebalancer
    GetCurrentAllocationCall, GetTargetAllocationCall, CalculateRebalanceCall,
    # Tax
    GetTransactionHistoryCall, CalculateTaxCall, GetTaxOptimizationTipsCall,
    # Controller
    DelegateCall, GenerateReportCall, SendTelegramCall, SendToNotionCall,
)


# ── 에이전트 이름 → BAML 함수 라우팅 ─────────────────────────────────

def call_baml_agent(agent_name: str, portfolio_context: str,
                    conversation_history: str, task: str):
    """에이전트 이름에 따라 해당 BAML 함수를 호출하고 타입화된 결과를 반환"""
    kwargs = dict(
        portfolio_context=portfolio_context,
        conversation_history=conversation_history,
        task=task,
    )
    dispatch = {
        "monitor":    b.MonitorAgentStep,
        "news":       b.NewsAgentStep,
        "trader":     b.TraderAgentStep,
        "rebalancer": b.RebalancerAgentStep,
        "tax":        b.TaxAgentStep,
        "controller": b.ControllerAgentStep,
    }
    fn = dispatch.get(agent_name)
    if fn is None:
        raise ValueError(f"알 수 없는 에이전트: {agent_name}")
    return fn(**kwargs)


# ── 이벤트 목록 → BAML 컨텍스트 문자열 ───────────────────────────────

def events_to_history_str(events: list) -> str:
    """
    이벤트 목록을 BAML 함수에 전달할 대화 이력 문자열로 변환.
    LLM이 이전 스텝 맥락을 이해할 수 있도록 간결하게 직렬화한다.
    """
    lines = []
    for event in events:
        name = type(event).__name__
        if name == "TaskStarted":
            lines.append(f"[작업 시작] {event.task}")
        elif name == "LLMResponded" and event.tool_name not in ("__parse_error__",):
            lines.append(f"[도구 선택] {event.tool_name} — {event.reason}")
        elif name == "ToolSucceeded":
            preview = event.result[:300] + "..." if len(event.result) > 300 else event.result
            lines.append(f"[결과] {event.tool_name}:\n{preview}")
        elif name == "ToolFailed":
            lines.append(f"[실패] {event.tool_name}: {event.error_msg}")
        elif name == "ToolRejected":
            lines.append(f"[거부] {event.tool_name}: {event.reject_reason}")
        elif name == "HumanAsked":
            lines.append(f"[질문] [{event.level}] {event.question}")
        elif name == "HumanResponded":
            lines.append(f"[답변] {event.answer}")
        elif name == "SubAgentCompleted":
            lines.append(f"[서브에이전트 완료] {event.agent_name} ({event.status}): {event.summary}")
        elif name == "CompactionEvent":
            lines.append(f"[요약] {event.summary}")
    return "\n".join(lines) if lines else "(이전 기록 없음)"


# ── BAML 결과 → validate_tool_call 호환 dict ─────────────────────────

def baml_result_to_dict(result) -> dict:
    """
    BAML Pydantic 결과를 validate_tool_call()이 기대하는
    {"tool": ..., "params": {...}, "reason": ...} 형태로 변환.
    """
    raw = result.model_dump()
    tool_name = raw.pop("tool", "")
    reason = raw.pop("reason", "")
    # SimulateSwapCall의 from_asset/to_asset을 기존 핸들러 키로 맞춤
    if tool_name == "simulate_swap":
        raw["from"] = raw.pop("from_asset", "")
        raw["to"] = raw.pop("to_asset", "")
    return {"tool": tool_name, "params": raw, "reason": reason}


# ── 타입 → 도구 이름 매핑 (이벤트 기록용) ────────────────────────────

def get_tool_name(result) -> str:
    """BAML 결과에서 tool 이름 추출"""
    return getattr(result, "tool", type(result).__name__.lower())
