# agents/base.py — Sub-Agent 실행 엔진 (BAML 통합)
#
# Factor 10: 각 에이전트는 작고 집중된 컨텍스트만 가진다 (3~10 스텝).
# Factor 7:  Agent→Agent 통신은 Agent→Human과 동일한 패턴.
# BAML 통합: LLM 호출 + 출력 파싱을 BAML이 담당.
#            이벤트 소싱(Factor 6·12)은 그대로 유지.

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from events import (TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
                    ToolRejected, HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed)
from reducer import should_compact, make_compaction_event, count_consecutive_errors
from tools import validate_tool_call
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id
from baml_bridge import call_baml_agent, events_to_history_str, baml_result_to_dict, get_tool_name
from baml_client.baml_client.types import AskHumanCall, DoneCall

log = setup_logger("sub_agent")


@dataclass
class SubAgentResult:
    """
    Sub-Agent 실행 결과.
    Factor 7: Agent→Agent 통신의 '응답' — 구조화된 출력.
    """
    agent_name: str
    status: str                   # "done" | "failed" | "max_steps_exceeded"
    summary: str = ""
    data: dict = field(default_factory=dict)
    run_id: str = ""
    steps: int = 0
    error: Optional[str] = None


def run_sub_agent(
    agent_name: str,
    task: str,
    snapshot: PortfolioSnapshot,
    config: AgentConfig,
    max_steps: int = 8,
    human_input_fn: Optional[Callable] = None,
    store: Optional[EventStore] = None,
    tools: Optional[dict[str, Callable]] = None,
) -> SubAgentResult:
    """
    BAML 기반 범용 Sub-Agent 실행 엔진.

    Factor 10: 각 Sub-Agent는 작은 max_steps (3~10)로 제한.
    Factor 12: 이벤트 소싱 패턴 유지 — BAML 결과도 이벤트로 기록.
    Factor 4:  BAML 결과 → validate_tool_call → 실행 파이프라인.

    Parameters:
        agent_name: 에이전트 이름 ("monitor", "news", "trader", ...)
        task:       이 에이전트에게 부여된 구체적 작업
        tools:      이 에이전트가 사용할 도구 맵 {name: handler_fn}
        max_steps:  최대 스텝 수 (Factor 10: 작게 유지)
    """
    if human_input_fn is None:
        human_input_fn = lambda level, q, ctx="": "자동 승인"
    if tools is None:
        tools = {}

    store = store or EventStore()
    run_id = new_run_id()
    store.start_run(run_id, f"{agent_name}:{task}")

    start_event = TaskStarted(task=task, portfolio_summary=snapshot.to_context_summary())
    store.append(run_id, start_event)
    events = [start_event]

    portfolio_ctx = snapshot.to_context_summary()
    log.info(f"[{agent_name}] 시작: {run_id} | task={task} | max_steps={max_steps}")

    try:
        for step in range(max_steps):
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            # ── BAML 호출: LLM 호출 + 타입 안전 파싱을 BAML이 담당 ──
            history = events_to_history_str(events)
            result = call_baml_agent(agent_name, portfolio_ctx, history, task)

            tool_name = get_tool_name(result)
            tool_dict = baml_result_to_dict(result)

            llm_event = LLMResponded(
                raw_output=result.model_dump_json(),
                tool_name=tool_name,
                tool_params=json.dumps(tool_dict.get("params", {}), ensure_ascii=False),
                reason=tool_dict.get("reason", ""),
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            log.info(f"[{agent_name}][{step+1}] {tool_name} — {tool_dict.get('reason', '')}")

            # ── done → Sub-Agent 완료 ────────────────────────────────
            if isinstance(result, DoneCall):
                done_event = AgentCompleted(summary=result.summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"[{agent_name}] 완료: {result.summary[:80]}")
                return SubAgentResult(
                    agent_name=agent_name, status="done",
                    summary=result.summary, run_id=run_id, steps=step + 1
                )

            # ── ask_human → 사람에게 에스컬레이션 ───────────────────
            if isinstance(result, AskHumanCall):
                asked = HumanAsked(
                    level=result.level,
                    question=result.question,
                    context=result.context or "",
                )
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(result.level, result.question, result.context or "")
                resp = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            # ── Factor 4: 검증 (Selection → Validation → Execution) ─
            validation = validate_tool_call(tool_dict, config)
            if not validation.approved:
                reject = ToolRejected(
                    tool_name=tool_name,
                    reject_reason=validation.reject_reason or "검증 실패",
                    original_params=json.dumps(tool_dict.get("params", {}), ensure_ascii=False),
                )
                store.append(run_id, reject)
                events.append(reject)
                continue

            # ── 실행: Sub-Agent 전용 도구 맵 사용 ───────────────────
            try:
                handler = tools.get(tool_name)
                if handler is None:
                    raise ValueError(f"[{agent_name}] 알 수 없는 도구: {tool_name}")
                exec_result = handler(tool_dict.get("params", {}))

                result_str = (
                    exec_result.get("display", json.dumps(exec_result, ensure_ascii=False))
                    if isinstance(exec_result, dict) else str(exec_result)
                )
                ok_event = ToolSucceeded(tool_name=tool_name, result=result_str)
                store.append(run_id, ok_event)
                events.append(ok_event)

            except Exception as e:
                err = ToolFailed(
                    tool_name=tool_name,
                    error_type=type(e).__name__,
                    error_msg=str(e)[:200],
                )
                store.append(run_id, err)
                events.append(err)

                # Factor 9: 연속 에러 에스컬레이션
                consecutive = count_consecutive_errors(events)
                if consecutive >= config.error_handling.max_consecutive_errors:
                    if config.error_handling.escalate_to_human:
                        question = (
                            f"[{agent_name}] 연속 {consecutive}회 에러. "
                            f"마지막: {type(e).__name__}: {str(e)[:100]}. 계속할까요?"
                        )
                        asked = HumanAsked(level="critical", question=question,
                                           context=f"연속 에러 {consecutive}회",
                                           urgency="high", response_format="yes_no")
                        store.append(run_id, asked)
                        events.append(asked)
                        answer = human_input_fn("critical", question, f"연속 에러 {consecutive}회")
                        resp = HumanResponded(answer=answer)
                        store.append(run_id, resp)
                        events.append(resp)
                        if answer.lower() in ("아니오", "no", "n", "중단"):
                            fail = AgentFailed(error=f"사용자가 중단: 연속 에러 {consecutive}회")
                            store.append(run_id, fail)
                            return SubAgentResult(
                                agent_name=agent_name, status="failed",
                                error=str(e), run_id=run_id, steps=step + 1
                            )
                    else:
                        fail = AgentFailed(error=f"연속 에러 {consecutive}회: {e}")
                        store.append(run_id, fail)
                        return SubAgentResult(
                            agent_name=agent_name, status="failed",
                            error=str(e), run_id=run_id, steps=step + 1
                        )

        store.append(run_id, AgentFailed(error="max_steps_exceeded"))
        return SubAgentResult(
            agent_name=agent_name, status="max_steps_exceeded",
            run_id=run_id, steps=max_steps,
            summary=f"{agent_name}: 최대 스텝({max_steps}) 도달",
        )

    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        return SubAgentResult(agent_name=agent_name, status="failed",
                              error=str(e), run_id=run_id)
