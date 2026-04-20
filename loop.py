# loop.py — Factor 6·7·8·9·10·12 통합 구현
#
# Factor 6:  Selection/Execution 분리 — LLM 선택 → 검증 → 실행
# Factor 7:  Outer Loop — 에이전트가 사람에게 도움을 요청
# Factor 8:  세 가지 제어 흐름 (sync, async break, approval)
# Factor 9:  연속 에러 카운터 + 사람 에스컬레이션
# Factor 10: 스텝 수 경고 — 컨텍스트 열화 방지
# Factor 12: Stateless Reducer — LLM 함수 주입 가능

import json
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional, TypedDict
import anthropic

from events import (TaskStarted, SnapshotRefreshed, LLMResponded,
                    ToolRejected, ToolSucceeded, ToolFailed,
                    HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed)
from reducer import (derive_context, derive_native_context,
                     should_compact, make_compaction_event,
                     count_consecutive_errors, count_steps)
from tools import parse_tool_call, validate_tool_call, ToolExecutor
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id

log = setup_logger("loop")

# Factor 12: LLM 호출을 추상화 — 테스트 시 ScriptedLLM으로 교체 가능
class Message(TypedDict):
    """Anthropic API 호환 메시지 형식 (content는 str 또는 블록 list)."""
    role: str        # "user" | "assistant"
    content: object  # str | list[dict] (cache_control 적용 시)


# LLM 호출 반환: (응답 텍스트, 사용량 정보). 사용량은 없을 수 있음(ScriptedLLM 등).
LLMUsage = dict  # {input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens}
LLMCallFn = Callable[[list[Message]], "str | tuple[str, LLMUsage]"]


def make_anthropic_llm(model: str, enable_prompt_cache: bool = True,
                       use_native_tools: bool = True) -> LLMCallFn:
    """
    Claude API 호출 함수 팩토리 (Phase 5: 진짜 네이티브 tool_use 프로토콜).

    반환 계약: (text, usage_dict, tool_use_id) 튜플.
      - text: parse_tool_call이 읽을 수 있는 JSON 문자열
      - usage_dict: input/output/cache 토큰 수
      - tool_use_id: Anthropic이 발급한 왕복 ID (tool_result 로 매칭하는 키)
    하위 호환: 문자열만 반환하는 ScriptedLLM 도 허용한다 (_invoke_llm에서 정규화).

    Phase 5 핵심 변경:
      - system_prompt 는 `system=` 파라미터로 전달 (Role Hacking 제거).
        기존에는 "사용자 메시지 + '네, JSON만 반환하겠습니다.'" 로 prefill 하던
        방식이었는데, 이는 Anthropic 공식 권장이 아니며 모델이 시스템 지시를
        사용자 발화로 혼동할 여지가 있다.
      - cache_control 을 system 블록과 마지막 tool 스키마에 적용.
        Phase 1 의 "첫 user 메시지 캐싱"은 Role Hacking 우회용이었다 (본질적
        차선책). Phase 5 에선 네이티브 경로에 맞춰 더 큰 프리픽스(system +
        tools)를 캐시한다.

    use_native_tools=True (기본):
      TOOL_SCHEMAS 를 tools 파라미터로 전달. 모델은 tool_use 블록으로 응답.
    """
    client = anthropic.Anthropic()

    def call(messages, system: Optional[str] = None):
        kwargs = {"model": model, "max_tokens": 1024, "messages": messages}
        if system:
            kwargs["system"] = _system_blocks(system) if enable_prompt_cache else system
        if use_native_tools:
            from tool_schemas import TOOL_SCHEMAS
            kwargs["tools"] = _tools_with_cache(TOOL_SCHEMAS) if enable_prompt_cache else TOOL_SCHEMAS
        response = client.messages.create(**kwargs)
        u = response.usage
        usage = {
            "input_tokens": getattr(u, "input_tokens", 0) or 0,
            "output_tokens": getattr(u, "output_tokens", 0) or 0,
            "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        }
        text = _extract_text_from_response(response, use_native_tools)
        tool_use_id = _extract_tool_use_id(response) if use_native_tools else ""
        return text, usage, tool_use_id
    return call


def _system_blocks(system_prompt: str) -> list[dict]:
    """system 프롬프트를 캐시 블록 형태로 감싸기."""
    return [{
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    }]


def _tools_with_cache(tool_schemas: list[dict]) -> list[dict]:
    """
    tools 배열의 마지막 스키마에 cache_control 적용.
    Anthropic의 tools caching 은 "마지막으로 표시된 블록까지" 한꺼번에 캐시한다.
    도구 스키마는 run 동안 변하지 않으므로 히트율이 매우 높다.
    """
    if not tool_schemas:
        return tool_schemas
    cached = list(tool_schemas)
    last = dict(cached[-1])
    last["cache_control"] = {"type": "ephemeral"}
    cached[-1] = last
    return cached


def _extract_text_from_response(response, use_native_tools: bool) -> str:
    """
    Anthropic 응답을 parse_tool_call이 읽을 수 있는 JSON 텍스트로 정규화.

    - tool_use 블록이 있으면 {"tool", "params", "reason"} 형태로 직렬화.
      reason은 함께 반환된 text 블록이 있으면 채운다 (Claude의 사고 과정).
    - tool_use가 없으면 text 블록 그대로 반환 (JSON-in-text 레거시 경로).
    """
    if not use_native_tools:
        return response.content[0].text

    text_reason = ""
    tool_use = None
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "text":
            text_reason = (text_reason + " " + block.text).strip()
        elif btype == "tool_use":
            tool_use = block
    if tool_use is None:
        return response.content[0].text if response.content else ""
    payload = {
        "tool": tool_use.name,
        "params": dict(tool_use.input or {}),
        "reason": text_reason,
    }
    return json.dumps(payload, ensure_ascii=False)


def _extract_tool_use_id(response) -> str:
    """tool_use 블록의 id 를 추출 (왕복 매칭용). 없으면 빈 문자열."""
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            return getattr(block, "id", "") or ""
    return ""


def _invoke_llm(llm_fn: LLMCallFn, messages: list,
                system: Optional[str] = None) -> tuple[str, dict, str]:
    """
    llm_fn 결과를 (text, usage, tool_use_id) 튜플로 정규화.
    ScriptedLLM 등 문자열만 반환하는 함수와 호환을 유지한다.

    Phase 5: 네이티브 경로에서 system= 를 전달한다. llm_fn 이 system 키워드를
    받지 않으면 (= 레거시 ScriptedLLM) 자동으로 위치 인자만 사용.
    """
    try:
        out = llm_fn(messages, system=system) if system is not None else llm_fn(messages)
    except TypeError:
        # 레거시 ScriptedLLM: system 키워드 미지원
        out = llm_fn(messages)
    if isinstance(out, tuple):
        if len(out) >= 3:
            return out[0], out[1] or {}, out[2] or ""
        return out[0], out[1] or {}, ""
    return out, {}, ""


def run_agent(
    snapshot: PortfolioSnapshot,
    task: str,
    config: AgentConfig,
    human_input_fn: Optional[Callable] = None,
    llm_fn: Optional[LLMCallFn] = None,
    resume_run_id: Optional[str] = None,
    store: Optional[EventStore] = None,
) -> dict:
    """
    메인 에이전트 루프.

    Factor 6:  Selection(LLM) → Validation → Execution 분리
    Factor 8:  세 가지 제어 흐름 패턴 구현
      - done → return (sync 완료)
      - ask_human → 사람 응답 대기 (async break)
      - 도구 실행 → continue (다음 스텝)
    Factor 9:  연속 에러 카운터 + 사람 에스컬레이션
    Factor 10: 스텝 수 경고 (컨텍스트 열화 방지)
    Factor 12: llm_fn 주입으로 테스트 가능
    """
    if human_input_fn is None:
        human_input_fn = _cli_human_input
    if llm_fn is None:
        llm_fn = make_anthropic_llm(config.model)

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
        for step in range(config.max_steps):
            # ── Factor 10: 컨텍스트 압축 ─────────────────────────
            if should_compact(events, config.context.max_context_messages):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            # ── Step 1: LLM 호출 (Selection) ─────────────────────
            # Phase 5: 네이티브 경로 — system 프롬프트를 별도 파라미터로,
            # messages 는 content-block 배열로 구성. Role Hacking 제거.
            if config.context.context_format == "native":
                system_prompt, messages = derive_native_context(events)
            else:
                system_prompt = None
                messages = derive_context(events, config.context.context_format)
            raw_output, usage, tool_use_id = _invoke_llm(
                llm_fn, messages, system=system_prompt,
            )

            # Factor 7: LLM은 반드시 JSON만 출력 — 파싱 실패 시 에러로 처리
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
                log.warning(f"[{step+1}] JSON 파싱 실패 — LLM에게 재시도 요청")
                continue

            tool_name  = tool_call.get("tool", "unknown")

            # Factor 6: LLM 응답을 실행 전에 기록 (Selection/Execution 분리).
            # Phase 5: tool_use_id 도 함께 저장 — 없으면 루프가 발급해서
            # 네이티브 reducer 가 tool_result 왕복을 구성할 수 있게 한다.
            effective_tool_use_id = tool_use_id or f"synthetic_{uuid.uuid4().hex[:12]}"
            llm_event = LLMResponded(
                raw_output=raw_output, tool_name=tool_name,
                tool_params=json.dumps(tool_call.get("params", {}), ensure_ascii=False),
                reason=tool_call.get("reason", ""),
                tool_use_id=effective_tool_use_id,
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_tokens", 0),
                cache_creation_tokens=usage.get("cache_creation_tokens", 0),
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            cache_hit_pct = _cache_hit_pct(usage)
            log.info(
                f"[{step+1}] {tool_name} — {tool_call.get('reason', '')} "
                f"| in={usage.get('input_tokens', 0)} out={usage.get('output_tokens', 0)} "
                f"cache_hit={cache_hit_pct:.0f}%",
                extra={"step": step+1, "tool": tool_name, **usage},
            )

            # ── Factor 10: 스텝 수 경고 ──────────────────────────
            current_steps = count_steps(events)
            if current_steps >= config.context.step_warning_threshold:
                log.warning(f"스텝 경고: {current_steps}/{config.max_steps}")

            # ── Factor 8 패턴 1: done → 동기 완료 ────────────────
            if tool_name == "done":
                summary    = tool_call["params"].get("summary", "")
                done_event = AgentCompleted(summary=summary)
                store.append(run_id, done_event)
                events.append(done_event)
                usage_totals = _summarize_usage(events)
                log.info(
                    f"완료: {run_id} | calls={usage_totals['llm_calls']} "
                    f"in={usage_totals['input']} out={usage_totals['output']} "
                    f"cache_hit={usage_totals['cache_hit_pct']:.0f}%",
                    extra={"run_id": run_id, **usage_totals},
                )
                return {"status": "done", "run_id": run_id, "summary": summary,
                        "steps": step + 1, "total_events": len(events),
                        "usage": usage_totals}

            # ── Factor 8 패턴 2: ask_human → 비동기 중단 ─────────
            if tool_name == "ask_human":
                p        = tool_call.get("params", {})
                level    = p.get("level", "info")
                question = p.get("question", "")
                ctx_info = p.get("context", "")
                asked    = HumanAsked(level=level, question=question, context=ctx_info)
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(level, question, ctx_info)
                resp   = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            # ── Step 2: 검증 (Factor 4: 도구 호출 = 제안) ────────
            validation = validate_tool_call(tool_call, config)
            if not validation.approved:
                approved_by_human = _handle_rejection(
                    validation, tool_call, tool_name,
                    run_id, store, events, human_input_fn,
                )
                if not approved_by_human:
                    continue
                # 사람이 승인 → 아래 실행 단계로 진행

            # ── Step 3: 실행 (Execution) ─────────────────────────
            try:
                result   = executor.dispatch(tool_call)
                ok_event = ToolSucceeded(tool_name=tool_name, result=result)
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


def _cache_hit_pct(usage: dict) -> float:
    """프리픽스 캐시 히트율 = cache_read / (cache_read + cache_creation)."""
    read = usage.get("cache_read_tokens", 0)
    create = usage.get("cache_creation_tokens", 0)
    total = read + create
    return (read / total * 100) if total else 0.0


def _summarize_usage(events: list) -> dict:
    """run 전체의 토큰 사용량 집계 (LLMResponded 이벤트 합산)."""
    totals = {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0, "llm_calls": 0}
    for e in events:
        if isinstance(e, LLMResponded):
            totals["input"] += e.input_tokens
            totals["output"] += e.output_tokens
            totals["cache_read"] += e.cache_read_tokens
            totals["cache_creation"] += e.cache_creation_tokens
            totals["llm_calls"] += 1
    cache_total = totals["cache_read"] + totals["cache_creation"]
    totals["cache_hit_pct"] = (
        totals["cache_read"] / cache_total * 100 if cache_total else 0.0
    )
    return totals


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
