# loop.py — Factor 6·7·8·9·10·12 통합 구현
#
# Factor 6:  Selection/Execution 분리 — LLM 선택 → 검증 → 실행
# Factor 7:  Outer Loop — 에이전트가 사람에게 도움을 요청
# Factor 8:  세 가지 제어 흐름 (sync, async break, approval)
# Factor 9:  연속 에러 카운터 + 사람 에스컬레이션
# Factor 10: 스텝 수 경고 — 컨텍스트 열화 방지
# Factor 12: Stateless Reducer — LLM 함수 주입 가능

import json
from datetime import datetime, timezone
from typing import Callable, Optional

from events import (TaskStarted, SnapshotRefreshed, LLMResponded,
                    ToolRejected, ToolSucceeded, ToolFailed,
                    HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed)
from reducer import (derive_context, should_compact, make_compaction_event,
                     count_consecutive_errors, count_steps)
from tools import validate_tool_call, ToolExecutor
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id
from baml_bridge import call_baml_agent, events_to_history_str, baml_result_to_dict, get_tool_name
from baml_client.baml_client.types import AskHumanCall, DoneCall

log = setup_logger("loop")


def run_agent(
    snapshot: PortfolioSnapshot,
    task: str,
    config: AgentConfig,
    human_input_fn: Optional[Callable] = None,
    resume_run_id: Optional[str] = None,
    store: Optional[EventStore] = None,
) -> dict:
    """
    BAML 기반 메인 에이전트 루프 (단일 에이전트 모드).

    Factor 6:  Selection(BAML) → Validation → Execution 분리
    Factor 8:  세 가지 제어 흐름 패턴 구현
      - done → return (sync 완료)
      - ask_human → 사람 응답 대기 (async break)
      - 도구 실행 → continue (다음 스텝)
    Factor 9:  연속 에러 카운터 + 사람 에스컬레이션
    Factor 10: 스텝 수 경고 (컨텍스트 열화 방지)
    BAML 통합: LLM 호출 + 파싱을 ControllerAgentStep BAML 함수가 담당.
    """
    if human_input_fn is None:
        human_input_fn = _cli_human_input

    store    = store or EventStore()
    executor = ToolExecutor(snapshot, config)

    # ── Factor 6: Launch / Resume ────────────────────────────────
    if resume_run_id:
        run_id = resume_run_id
        events = store.load(run_id)
        log.info(f"재개: {run_id} ({len(events)}개 이벤트 replay)")

        # Factor 6 개선: 스냅샷 신선도 검사
        stale = _check_snapshot_staleness(events, config)
        if stale > 0:
            refresh_event = SnapshotRefreshed(
                portfolio_summary=snapshot.to_context_summary(),
                stale_minutes=stale
            )
            store.append(run_id, refresh_event)
            events.append(refresh_event)
            log.info(f"스냅샷 갱신: {stale}분 경과 → 새 데이터로 교체")
    else:
        run_id = new_run_id()
        store.start_run(run_id, task)
        start_event = TaskStarted(
            task=task,
            portfolio_summary=snapshot.to_context_summary()
        )
        store.append(run_id, start_event)
        events = [start_event]
        log.info(f"시작: {run_id} | task={task} | ${snapshot.total_value_usd:,.2f}")

    try:
        portfolio_ctx = snapshot.to_context_summary()

        for step in range(config.max_steps):
            # ── Factor 10: 컨텍스트 압축 ─────────────────────────
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            # ── Step 1: BAML 호출 (Selection + 파싱) ────────────
            history = events_to_history_str(events)
            result  = call_baml_agent("controller", portfolio_ctx, history, task)

            tool_name = get_tool_name(result)
            tool_dict = baml_result_to_dict(result)

            # Factor 6: BAML 응답을 실행 전에 이벤트로 기록 (Selection/Execution 분리)
            llm_event = LLMResponded(
                raw_output=result.model_dump_json(),
                tool_name=tool_name,
                tool_params=json.dumps(tool_dict.get("params", {}), ensure_ascii=False),
                reason=tool_dict.get("reason", ""),
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            log.info(f"[{step+1}] {tool_name} — {tool_dict.get('reason', '')}",
                     extra={"step": step+1, "tool": tool_name})

            # ── Factor 10: 스텝 수 경고 ──────────────────────────
            current_steps = count_steps(events)
            if current_steps >= config.context.step_warning_threshold:
                log.warning(f"스텝 경고: {current_steps}/{config.max_steps}")

            # ── Factor 8 패턴 1: done → 동기 완료 ────────────────
            if isinstance(result, DoneCall):
                done_event = AgentCompleted(summary=result.summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"완료: {run_id}")
                return {"status": "done", "run_id": run_id, "summary": result.summary,
                        "steps": step + 1, "total_events": len(events)}

            # ── Factor 8 패턴 2: ask_human → 비동기 중단 ─────────
            if isinstance(result, AskHumanCall):
                asked = HumanAsked(level=result.level, question=result.question,
                                   context=result.context or "")
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(result.level, result.question, result.context or "")
                resp   = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            # ── Step 2: 검증 (Factor 4: 도구 호출 = 제안) ────────
            validation = validate_tool_call(tool_dict, config)
            if not validation.approved:
                approved_by_human = _handle_rejection(
                    validation, tool_dict, tool_name,
                    run_id, store, events, human_input_fn,
                )
                if not approved_by_human:
                    continue

            # ── Step 3: 실행 (Execution) ─────────────────────────
            try:
                exec_result = executor.dispatch(tool_dict)
                ok_event    = ToolSucceeded(tool_name=tool_name, result=exec_result)
                store.append(run_id, ok_event)
                events.append(ok_event)
                log.debug(f"도구 성공: {tool_name}")
            except Exception as e:
                _handle_tool_error(
                    e, tool_name, run_id, store, events,
                    human_input_fn, config,
                )

        # max_steps 도달
        fail = AgentFailed(error="max_steps_exceeded")
        store.append(run_id, fail)
        return {"status": "max_steps_exceeded", "run_id": run_id,
                "total_events": len(events)}

    except KeyboardInterrupt:
        return {"status": "paused", "run_id": run_id,
                "resume_cmd": f"python main.py --resume {run_id}"}
    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        log.exception(f"예외: {e}")
        raise


_HUMAN_APPROVAL_WORDS = {"네", "yes", "승인", "확인", "y"}


def _handle_rejection(validation, tool_call: dict, tool_name: str,
                      run_id: str, store: EventStore, events: list,
                      human_input_fn: Callable) -> bool:
    """
    검증 실패 처리. 반환값은 "최종적으로 실행을 진행할지".
    - requires_human=True + 사람이 승인 → True (실행 진행)
    - requires_human=True + 사람이 거부 → False (ToolRejected 기록, continue)
    - requires_human=False → False (자동 거부, ToolRejected 기록, continue)
    """
    params_json = json.dumps(tool_call.get("params", {}), ensure_ascii=False)

    if not validation.requires_human:
        reject = ToolRejected(
            tool_name=tool_name,
            reject_reason=validation.reject_reason or "검증 실패",
            original_params=params_json,
        )
        store.append(run_id, reject)
        events.append(reject)
        log.warning(f"도구 거부: {tool_name} — {validation.reject_reason}")
        return False

    question = validation.human_question or "도구 실행 승인이 필요합니다."
    tool_json = json.dumps(tool_call, ensure_ascii=False)
    asked = HumanAsked(level="warning", question=question, context=tool_json)
    store.append(run_id, asked)
    events.append(asked)
    answer = human_input_fn("warning", question, tool_json)
    resp = HumanResponded(answer=answer)
    store.append(run_id, resp)
    events.append(resp)

    if answer.strip().lower() in _HUMAN_APPROVAL_WORDS:
        return True

    reject = ToolRejected(
        tool_name=tool_name,
        reject_reason=f"사용자 거부: {answer}",
        original_params=params_json,
    )
    store.append(run_id, reject)
    events.append(reject)
    return False


def _handle_tool_error(e: Exception, tool_name: str, run_id: str,
                       store: EventStore, events: list,
                       human_input_fn: Callable, config: AgentConfig) -> None:
    """
    도구 실행 실패 기록 + Factor 9 연속 에러 에스컬레이션.
    """
    err = ToolFailed(
        tool_name=tool_name,
        error_type=type(e).__name__,
        error_msg=str(e)[:config.error_handling.max_error_msg_len],
    )
    store.append(run_id, err)
    events.append(err)
    log.warning(f"도구 실패: {tool_name} — {e}")

    consecutive = count_consecutive_errors(events)
    if not (consecutive >= config.error_handling.max_consecutive_errors
            and config.error_handling.escalate_to_human):
        return

    log.error(f"연속 에러 {consecutive}회 → 사람 에스컬레이션")
    question = (
        f"연속 {consecutive}회 에러 발생. "
        f"마지막 에러: {type(e).__name__}: {str(e)[:100]}. "
        f"계속 진행할까요?"
    )
    ctx = f"연속 에러 {consecutive}회"
    asked = HumanAsked(level="critical", question=question, context=ctx)
    store.append(run_id, asked)
    events.append(asked)
    answer = human_input_fn("critical", question, ctx)
    resp = HumanResponded(answer=answer)
    store.append(run_id, resp)
    events.append(resp)


def _check_snapshot_staleness(events: list, config: AgentConfig) -> int:
    """
    Factor 6: 재개 시 스냅샷 신선도 검사.
    TaskStarted의 timestamp와 현재 시간을 비교.
    반환: 경과 분 (stale_minutes 임계값 초과 시), 0이면 신선함.
    """
    for event in events:
        if isinstance(event, TaskStarted):
            try:
                start_time = datetime.fromisoformat(event.timestamp)
                now = datetime.now(timezone.utc)
                elapsed_minutes = int((now - start_time).total_seconds() / 60)
                if elapsed_minutes > config.context.snapshot_stale_minutes:
                    return elapsed_minutes
            except (ValueError, TypeError):
                pass
            break
    return 0


def replay_at(run_id: str, seq: int, store: Optional[EventStore] = None,
              context_format: str = "xml") -> list[dict]:
    """특정 시점의 context 재현 (타임머신)"""
    store  = store or EventStore()
    events = store.load_until(run_id, seq)
    return derive_context(events, context_format)


def _cli_human_input(level: str, question: str, context: str = "") -> str:
    """CLI 환경에서 사람 입력 받기"""
    icon = {"info": "i", "warning": "!", "critical": "!!!"}.get(level, "?")
    print(f"\n[{icon}] [{level.upper()}] {question}")
    if context:
        print(f"   상세: {context}")
    return input("   답변: ").strip()


def _auto_approve_input(level: str, question: str, context: str = "") -> str:
    """자동 승인 모드 (테스트/자동화용)"""
    return "자동 승인" if level != "critical" else "자동 승인 불가"
