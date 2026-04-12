# agents/controller.py — Controller Agent (오케스트레이터)
#
# Factor 10 원문 핵심 적용:
#   "DeFi 포트폴리오 관리 + 뉴스 분석 + 트레이딩 + 리밸런싱 + 세금 계산"
#   → 하나의 에이전트가 아닌, 5개 전문 Sub-Agent + Controller.
#
#   Controller는 사용자 요청을 분석하고,
#   적절한 Sub-Agent에게 작업을 위임(delegate)하고,
#   결과를 종합하여 최종 응답을 생성한다.
#
# Factor 7 확장:
#   Agent→Agent 통신 = Agent→Human과 동일한 패턴.
#   Controller가 Sub-Agent를 "호출"하는 것은
#   에이전트가 사람에게 "질문"하는 것과 같은 구조.

import json
from typing import Callable, Optional

from agents.base import run_sub_agent, SubAgentResult, LLMCallFn
from agents.registry import get_all_agent_specs, SubAgentSpec
from events import (TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
                    HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed,
                    SubAgentStarted, SubAgentCompleted)
from reducer import derive_context, should_compact, make_compaction_event
from tools import parse_tool_call, validate_tool_call, ValidationResult
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id

log = setup_logger("controller")


CONTROLLER_PROMPT = """
당신은 DeFi 포트폴리오 관리 총괄 Controller Agent입니다.
당신은 직접 작업을 수행하지 않습니다. 전문 Sub-Agent에게 위임합니다.

## 사용 가능한 Sub-Agent
- monitor: 포트폴리오 모니터링 + 이상 징후 탐지
- news: 뉴스 수집 + 감성 분석
- trader: 트레이딩 분석 + 스왑 시뮬레이션 (실제 거래 X)
- rebalancer: 포트폴리오 리밸런싱 분석
- tax: 세금 계산 + 최적화 권고

## 도구 (JSON 형식으로만 반환)
{"tool": "delegate", "params": {"agent": "monitor|news|trader|rebalancer|tax", "task": "구체적 작업 지시"}, "reason": "위임 이유"}
{"tool": "generate_report", "params": {"type": "daily|weekly|alert"}, "reason": "종합 리포트 생성"}
{"tool": "send_telegram_alert", "params": {"message": "...", "level": "info|warning|critical"}, "reason": "알림 발송"}
{"tool": "send_to_notion", "params": {"report_id": "..."}, "reason": "Notion 저장"}
{"tool": "ask_human", "params": {"level": "...", "question": "...", "context": "..."}, "reason": "사람 확인"}
{"tool": "done", "params": {"summary": "..."}, "reason": "모든 작업 완료"}

## 작업 전략
1. 사용자 요청을 분석하여 필요한 Sub-Agent 목록을 결정
2. 각 Sub-Agent에게 구체적 작업을 위임 (delegate)
3. Sub-Agent 결과를 종합
4. 필요 시 리포트 생성 또는 알림 발송
5. 최종 요약으로 완료

## 규칙
- JSON만 반환, 텍스트 금지
- 한 번에 하나의 도구만 선택
- Sub-Agent 결과를 읽고 다음 행동을 결정
- critical 수준 알림은 반드시 ask_human 후 발송
""".strip()


def run_controller(
    snapshot: PortfolioSnapshot,
    task: str,
    config: AgentConfig,
    llm_fn: LLMCallFn,
    human_input_fn: Optional[Callable] = None,
    store: Optional[EventStore] = None,
) -> dict:
    """
    Controller Agent 실행.

    Factor 10: Controller 자체도 작은 컨텍스트 (max_steps).
               각 delegate 호출은 Sub-Agent의 독립 실행.
    Factor 7:  delegate = Agent→Agent 통신 (Agent→Human과 동일 패턴).
    """
    if human_input_fn is None:
        human_input_fn = lambda level, q, ctx="": "자동 승인"

    store = store or EventStore()
    run_id = new_run_id()
    store.start_run(run_id, f"controller:{task}")

    agent_specs = get_all_agent_specs(snapshot)

    start_event = TaskStarted(
        task=task,
        portfolio_summary=snapshot.to_context_summary()
    )
    store.append(run_id, start_event)
    events = [start_event]

    log.info(f"[Controller] 시작: {run_id} | task={task}")

    # Controller 전용 context 생성
    def _derive_controller_context(evts):
        fmt = config.context.context_format
        # "single" 모드는 별도 처리
        if fmt == "single":
            msgs = derive_context(evts, fmt)
            if msgs and msgs[0]["role"] == "user":
                import re
                content = msgs[0]["content"]
                content = re.sub(
                    r"<system_instruction>.*?</system_instruction>",
                    f"<system_instruction>\n{CONTROLLER_PROMPT}\n</system_instruction>",
                    content, count=1, flags=re.DOTALL
                )
                msgs[0]["content"] = content
            return msgs

        messages = derive_context(evts, fmt)
        # 시스템 프롬프트 + assistant 확인 메시지 함께 교체
        if len(messages) >= 2 and messages[0]["role"] == "user":
            if fmt == "xml":
                messages[0]["content"] = f"<system_instruction>\n{CONTROLLER_PROMPT}\n</system_instruction>"
            else:
                messages[0]["content"] = CONTROLLER_PROMPT
            messages[1]["content"] = "네, Controller로서 JSON만 반환하고 Sub-Agent에게 위임하겠습니다."
        return messages

    # Controller 전용 도구: generate_report, send_telegram_alert, send_to_notion
    from tools import ToolExecutor
    executor = ToolExecutor(snapshot, config)

    controller_max_steps = config.max_steps  # Controller는 더 많은 스텝 허용

    try:
        for step in range(controller_max_steps):
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            messages   = _derive_controller_context(events)
            raw_output = llm_fn(messages)

            # Factor 7: JSON 파싱 실패 시 에러로 처리
            try:
                tool_call = parse_tool_call(raw_output)
            except ValueError as e:
                llm_event = LLMResponded(
                    raw_output=raw_output, tool_name="__parse_error__",
                    reason=str(e)[:200]
                )
                store.append(run_id, llm_event)
                events.append(llm_event)
                err = ToolFailed(
                    tool_name="__parse_error__",
                    error_type="JSONParseError",
                    error_msg=f"JSON 형식 오류. JSON만 출력하세요. 원본: {raw_output[:100]}"
                )
                store.append(run_id, err)
                events.append(err)
                log.warning(f"[Controller][{step+1}] JSON 파싱 실패")
                continue

            tool_name  = tool_call.get("tool", "unknown")

            llm_event = LLMResponded(
                raw_output=raw_output, tool_name=tool_name,
                tool_params=json.dumps(tool_call.get("params", {}), ensure_ascii=False),
                reason=tool_call.get("reason", "")
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            log.info(f"[Controller][{step+1}] {tool_name} — {tool_call.get('reason', '')}")

            # done → Controller 완료
            if tool_name == "done":
                summary = tool_call["params"].get("summary", "")
                done_event = AgentCompleted(summary=summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"[Controller] 완료: {summary[:80]}")
                return {"status": "done", "run_id": run_id, "summary": summary,
                        "steps": step + 1, "total_events": len(events)}

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

            # ── delegate → Sub-Agent 실행 (Factor 7: Agent→Agent) ──
            if tool_name == "delegate":
                params = tool_call.get("params", {})
                agent_name = params.get("agent", "")
                agent_task = params.get("task", "")

                spec = agent_specs.get(agent_name)
                if spec is None:
                    err_event = ToolFailed(
                        tool_name="delegate",
                        error_type="UnknownAgent",
                        error_msg=f"알 수 없는 에이전트: {agent_name}. 사용 가능: {list(agent_specs.keys())}"
                    )
                    store.append(run_id, err_event)
                    events.append(err_event)
                    continue

                # Sub-Agent 시작 이벤트 기록
                sub_started = SubAgentStarted(
                    agent_name=agent_name, task=agent_task
                )
                store.append(run_id, sub_started)
                events.append(sub_started)

                log.info(f"[Controller] → [{agent_name}] 위임: {agent_task}")

                # Sub-Agent 실행 (별도 run_id, 별도 이벤트 스트림)
                sub_result = run_sub_agent(
                    agent_name=spec.name,
                    system_prompt=spec.system_prompt,
                    tools=spec.tools,
                    task=agent_task,
                    snapshot=snapshot,
                    config=config,
                    llm_fn=llm_fn,
                    max_steps=spec.max_steps,
                    human_input_fn=human_input_fn,
                    store=store,
                )

                # Sub-Agent 결과를 Controller 이벤트로 기록
                sub_completed = SubAgentCompleted(
                    agent_name=agent_name,
                    status=sub_result.status,
                    summary=sub_result.summary,
                    sub_run_id=sub_result.run_id
                )
                store.append(run_id, sub_completed)
                events.append(sub_completed)

                log.info(f"[Controller] ← [{agent_name}] {sub_result.status}: {sub_result.summary[:60]}")
                continue

            # ── Factor 4: 기타 도구도 검증 후 실행 ──────────────────
            validation = validate_tool_call(tool_call, config)
            if not validation.approved:
                if validation.requires_human:
                    question = validation.human_question or "도구 실행 승인이 필요합니다."
                    asked = HumanAsked(level="warning", question=question,
                                       context=json.dumps(tool_call, ensure_ascii=False))
                    store.append(run_id, asked)
                    events.append(asked)
                    answer = human_input_fn("warning", question,
                                            json.dumps(tool_call, ensure_ascii=False))
                    resp = HumanResponded(answer=answer)
                    store.append(run_id, resp)
                    events.append(resp)
                    if answer.lower() not in ("네", "yes", "승인", "확인", "y"):
                        from events import ToolRejected
                        reject = ToolRejected(
                            tool_name=tool_name,
                            reject_reason=f"사용자 거부: {answer}",
                            original_params=json.dumps(tool_call.get("params", {}),
                                                       ensure_ascii=False)
                        )
                        store.append(run_id, reject)
                        events.append(reject)
                        continue
                else:
                    from events import ToolRejected
                    reject = ToolRejected(
                        tool_name=tool_name,
                        reject_reason=validation.reject_reason or "검증 실패",
                        original_params=json.dumps(tool_call.get("params", {}),
                                                   ensure_ascii=False)
                    )
                    store.append(run_id, reject)
                    events.append(reject)
                    log.warning(f"[Controller] 도구 거부: {tool_name} — {validation.reject_reason}")
                    continue

            try:
                result = executor.dispatch(tool_call)
                ok_event = ToolSucceeded(tool_name=tool_name, result=result)
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

        fail = AgentFailed(error="max_steps_exceeded")
        store.append(run_id, fail)
        return {"status": "max_steps_exceeded", "run_id": run_id,
                "total_events": len(events)}

    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        log.exception(f"[Controller] 예외: {e}")
        raise
