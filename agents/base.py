# agents/base.py — Sub-Agent 실행 엔진
#
# Factor 10 원문 핵심:
#   "As context grows, LLMs get lost."
#   해결: 각 에이전트는 작고 집중된 컨텍스트만 가진다 (3~10 스텝).
#
# Factor 7 확장:
#   Agent→Agent 통신은 Agent→Human과 동일한 패턴.
#   Controller가 Sub-Agent를 "호출"하는 것은
#   에이전트가 사람에게 "질문"하는 것과 같은 구조.

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from events import (TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
                    ToolRejected, HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed)
from reducer import (derive_context, should_compact, make_compaction_event,
                     count_consecutive_errors)
from tools import parse_tool_call, validate_tool_call
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id

log = setup_logger("sub_agent")

LLMCallFn = Callable[[list], str]


@dataclass
class SubAgentResult:
    """
    Sub-Agent 실행 결과.
    Factor 7: Agent→Agent 통신의 '응답' — 구조화된 출력.
    """
    agent_name: str
    status: str                   # "done" | "failed" | "max_steps_exceeded"
    summary: str = ""
    data: dict = field(default_factory=dict)  # 구조화된 결과 데이터
    run_id: str = ""
    steps: int = 0
    error: Optional[str] = None


def run_sub_agent(
    agent_name: str,
    system_prompt: str,
    tools: dict[str, Callable],
    task: str,
    snapshot: PortfolioSnapshot,
    config: AgentConfig,
    llm_fn: LLMCallFn,
    max_steps: int = 8,
    human_input_fn: Optional[Callable] = None,
    store: Optional[EventStore] = None,
) -> SubAgentResult:
    """
    범용 Sub-Agent 실행 엔진.

    Factor 10: 각 Sub-Agent는 작은 max_steps (3~10)로 제한.
    Factor 12: 같은 event sourcing 패턴 사용.
    Factor 4:  같은 Selection → Validation → Execution 파이프라인.

    Parameters:
        agent_name:    에이전트 이름 (예: "monitor", "news")
        system_prompt: 이 에이전트 전용 시스템 프롬프트 (Factor 2)
        tools:         이 에이전트가 사용할 도구 맵 {name: handler_fn}
        task:          이 에이전트에게 부여된 구체적 작업
        max_steps:     최대 스텝 수 (Factor 10: 작게 유지)
    """
    if human_input_fn is None:
        human_input_fn = lambda level, q, ctx="": "자동 승인"

    store = store or EventStore()
    run_id = new_run_id()
    store.start_run(run_id, f"{agent_name}:{task}")

    # TaskStarted 이벤트 — 에이전트 전용 프롬프트를 portfolio_summary에 포함
    start_event = TaskStarted(
        task=task,
        portfolio_summary=snapshot.to_context_summary()
    )
    store.append(run_id, start_event)
    events = [start_event]

    log.info(f"[{agent_name}] 시작: {run_id} | task={task} | max_steps={max_steps}")

    # Sub-Agent 전용 derive_context — system_prompt를 오버라이드
    def _derive_with_custom_prompt(evts):
        from prompts import XML_CONTEXT_TEMPLATES
        fmt = XML_CONTEXT_TEMPLATES
        messages = derive_context(evts, config.context.context_format)
        # 첫 번째 메시지(기본 시스템 프롬프트)를 이 에이전트 전용으로 교체
        if messages and messages[0]["role"] == "user":
            if config.context.context_format == "xml":
                messages[0]["content"] = f"<system_instruction>\n{system_prompt}\n</system_instruction>"
            else:
                messages[0]["content"] = system_prompt
        return messages

    collected_data = {}

    try:
        for step in range(max_steps):
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            messages   = _derive_with_custom_prompt(events)
            raw_output = llm_fn(messages)
            tool_call  = parse_tool_call(raw_output)
            tool_name  = tool_call.get("tool", "unknown")

            llm_event = LLMResponded(
                raw_output=raw_output, tool_name=tool_name,
                tool_params=json.dumps(tool_call.get("params", {}), ensure_ascii=False),
                reason=tool_call.get("reason", "")
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            log.info(f"[{agent_name}][{step+1}] {tool_name} — {tool_call.get('reason', '')}")

            # done → Sub-Agent 완료
            if tool_name == "done":
                params = tool_call.get("params", {})
                summary = params.get("summary", "")
                # data 필드가 있으면 구조화된 결과로 수집
                if "data" in params:
                    collected_data.update(params["data"])
                done_event = AgentCompleted(summary=summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"[{agent_name}] 완료: {summary[:80]}")
                return SubAgentResult(
                    agent_name=agent_name, status="done",
                    summary=summary, data=collected_data,
                    run_id=run_id, steps=step + 1
                )

            # ask_human → 사람에게 에스컬레이션
            if tool_name == "ask_human":
                p = tool_call.get("params", {})
                asked = HumanAsked(level=p.get("level", "info"),
                                   question=p.get("question", ""),
                                   context=p.get("context", ""))
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(p.get("level", "info"),
                                        p.get("question", ""),
                                        p.get("context", ""))
                resp = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            # 검증 (Factor 4)
            validation = validate_tool_call(tool_call, config)
            if not validation.approved:
                reject = ToolRejected(
                    tool_name=tool_name,
                    reject_reason=validation.reject_reason or "검증 실패",
                    original_params=json.dumps(tool_call.get("params", {}),
                                               ensure_ascii=False)
                )
                store.append(run_id, reject)
                events.append(reject)
                continue

            # 실행 — Sub-Agent 전용 도구 맵 사용
            try:
                handler = tools.get(tool_name)
                if handler is None:
                    raise ValueError(f"[{agent_name}] 알 수 없는 도구: {tool_name}")
                result = handler(tool_call.get("params", {}))

                # 결과에서 데이터 수집
                if isinstance(result, dict):
                    collected_data.update(result.get("_data", {}))
                    result_str = result.get("display", json.dumps(result, ensure_ascii=False))
                else:
                    result_str = str(result)

                ok_event = ToolSucceeded(tool_name=tool_name, result=result_str)
                store.append(run_id, ok_event)
                events.append(ok_event)
            except Exception as e:
                err = ToolFailed(
                    tool_name=tool_name,
                    error_type=type(e).__name__,
                    error_msg=str(e)[:200]
                )
                store.append(run_id, err)
                events.append(err)

                consecutive = count_consecutive_errors(events)
                if consecutive >= config.error_handling.max_consecutive_errors:
                    log.error(f"[{agent_name}] 연속 에러 {consecutive}회 → 실패 처리")
                    fail = AgentFailed(error=f"연속 에러 {consecutive}회: {e}")
                    store.append(run_id, fail)
                    return SubAgentResult(
                        agent_name=agent_name, status="failed",
                        error=str(e), run_id=run_id, steps=step + 1
                    )

        # max_steps 도달
        store.append(run_id, AgentFailed(error="max_steps_exceeded"))
        return SubAgentResult(
            agent_name=agent_name, status="max_steps_exceeded",
            run_id=run_id, steps=max_steps,
            summary=f"{agent_name}: 최대 스텝({max_steps}) 도달"
        )

    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        return SubAgentResult(
            agent_name=agent_name, status="failed",
            error=str(e), run_id=run_id
        )
