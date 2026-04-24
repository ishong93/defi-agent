# agents/controller.py — Controller Agent (BAML 통합)
#
# Factor 10: Controller는 직접 작업하지 않고 Sub-Agent에게 위임.
# Factor 7:  Agent→Agent 통신 = Agent→Human과 동일한 패턴.
# BAML 통합: ControllerAgentStep BAML 함수가 LLM 호출 + 파싱을 담당.

import json
from typing import Callable, Optional

from agents.base import run_sub_agent, SubAgentResult
from agents.registry import get_all_agent_specs
from events import (TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
                    ToolRejected, HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed,
                    SubAgentStarted, SubAgentCompleted)
from reducer import should_compact, make_compaction_event
from tools import validate_tool_call
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id
from baml_bridge import call_baml_agent, events_to_history_str, baml_result_to_dict, get_tool_name
from baml_client.baml_client.types import (
    AskHumanCall, DoneCall, DelegateCall,
    GenerateReportCall, SendTelegramCall, SendToNotionCall,
)

log = setup_logger("controller")


def run_controller(
    snapshot: PortfolioSnapshot,
    task: str,
    config: AgentConfig,
    human_input_fn: Optional[Callable] = None,
    store: Optional[EventStore] = None,
) -> dict:
    """
    BAML 기반 Controller Agent 실행.

    Factor 10: Controller 자체도 max_steps로 제한.
               각 delegate 호출은 Sub-Agent의 독립 실행.
    Factor 7:  delegate = Agent→Agent 통신.
    """
    if human_input_fn is None:
        human_input_fn = lambda level, q, ctx="": "자동 승인"

    store = store or EventStore()
    run_id = new_run_id()
    store.start_run(run_id, f"controller:{task}")

    agent_specs = get_all_agent_specs(snapshot)

    start_event = TaskStarted(task=task, portfolio_summary=snapshot.to_context_summary())
    store.append(run_id, start_event)
    events = [start_event]

    portfolio_ctx = snapshot.to_context_summary()
    from tools import ToolExecutor
    executor = ToolExecutor(snapshot, config)

    log.info(f"[Controller] 시작: {run_id} | task={task}")

    try:
        for step in range(config.max_steps):
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            # ── BAML 호출 ──────────────────────────────────────────
            history = events_to_history_str(events)
            result = call_baml_agent("controller", portfolio_ctx, history, task)

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
            log.info(f"[Controller][{step+1}] {tool_name} — {tool_dict.get('reason', '')}")

            # ── done ───────────────────────────────────────────────
            if isinstance(result, DoneCall):
                done_event = AgentCompleted(summary=result.summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"[Controller] 완료: {result.summary[:80]}")
                return {"status": "done", "run_id": run_id, "summary": result.summary,
                        "steps": step + 1, "total_events": len(events)}

            # ── ask_human ──────────────────────────────────────────
            if isinstance(result, AskHumanCall):
                asked = HumanAsked(level=result.level, question=result.question,
                                   context=result.context or "")
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(result.level, result.question, result.context or "")
                resp = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            # ── delegate → Sub-Agent 실행 (Factor 7: Agent→Agent) ──
            if isinstance(result, DelegateCall):
                spec = agent_specs.get(result.agent)
                if spec is None:
                    err_event = ToolFailed(
                        tool_name="delegate",
                        error_type="UnknownAgent",
                        error_msg=f"알 수 없는 에이전트: {result.agent}",
                    )
                    store.append(run_id, err_event)
                    events.append(err_event)
                    continue

                sub_started = SubAgentStarted(agent_name=result.agent, task=result.task)
                store.append(run_id, sub_started)
                events.append(sub_started)
                log.info(f"[Controller] → [{result.agent}] 위임: {result.task}")

                sub_result = run_sub_agent(
                    agent_name=spec.name,
                    task=result.task,
                    snapshot=snapshot,
                    config=config,
                    max_steps=spec.max_steps,
                    human_input_fn=human_input_fn,
                    store=store,
                    tools=spec.tools,
                )

                sub_completed = SubAgentCompleted(
                    agent_name=result.agent,
                    status=sub_result.status,
                    summary=sub_result.summary,
                    sub_run_id=sub_result.run_id,
                )
                store.append(run_id, sub_completed)
                events.append(sub_completed)
                log.info(f"[Controller] ← [{result.agent}] {sub_result.status}: {sub_result.summary[:60]}")
                continue

            # ── 나머지 도구: Factor 4 검증 후 실행 ─────────────────
            validation = validate_tool_call(tool_dict, config)
            if not validation.approved:
                if validation.requires_human:
                    question = validation.human_question or "도구 실행 승인이 필요합니다."
                    asked = HumanAsked(level="warning", question=question,
                                       context=json.dumps(tool_dict, ensure_ascii=False))
                    store.append(run_id, asked)
                    events.append(asked)
                    answer = human_input_fn("warning", question,
                                            json.dumps(tool_dict, ensure_ascii=False))
                    resp = HumanResponded(answer=answer)
                    store.append(run_id, resp)
                    events.append(resp)
                    if answer.lower() not in ("네", "yes", "승인", "확인", "y"):
                        reject = ToolRejected(
                            tool_name=tool_name,
                            reject_reason=f"사용자 거부: {answer}",
                            original_params=json.dumps(tool_dict.get("params", {}),
                                                       ensure_ascii=False),
                        )
                        store.append(run_id, reject)
                        events.append(reject)
                        continue
                else:
                    reject = ToolRejected(
                        tool_name=tool_name,
                        reject_reason=validation.reject_reason or "검증 실패",
                        original_params=json.dumps(tool_dict.get("params", {}),
                                                   ensure_ascii=False),
                    )
                    store.append(run_id, reject)
                    events.append(reject)
                    log.warning(f"[Controller] 도구 거부: {tool_name} — {validation.reject_reason}")
                    continue

            try:
                exec_result = executor.dispatch(tool_dict)
                ok_event = ToolSucceeded(tool_name=tool_name, result=exec_result)
                store.append(run_id, ok_event)
                events.append(ok_event)
            except Exception as e:
                err = ToolFailed(tool_name=tool_name, error_type=type(e).__name__,
                                 error_msg=str(e)[:200])
                store.append(run_id, err)
                events.append(err)

        fail = AgentFailed(error="max_steps_exceeded")
        store.append(run_id, fail)
        return {"status": "max_steps_exceeded", "run_id": run_id,
                "total_events": len(events)}

    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        log.exception(f"[Controller] 예외: {e}")
        raise
