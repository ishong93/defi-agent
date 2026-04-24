# reducer.py — Factor 12: Agent as a Stateless Reducer
#
# 핵심 원리:
#   derive_context(events, format) → LLM에 넘길 messages 배열
#
#   이 함수는 완전한 순수 함수다:
#   - 사이드 이펙트 없음
#   - 같은 이벤트 목록 + 같은 format → 항상 같은 context
#   - 테스트하기 매우 쉬움
#   - 이벤트 목록만 있으면 어느 시점이든 context 재현 가능
#
# Redux의 reducer와 정확히 같은 패턴:
#   Redux:  (state, action) → state
#   여기:   (events)        → context (messages[])
#
# Factor 3 (Own Your Context Window) 개선:
#   - XML 태그 형식과 평문 형식을 format 파라미터로 전환 가능
#   - Karpathy "context engineering" — 구조화된 구분자로 LLM 혼동 방지
#
# Factor 9 (Compact Errors) 개선:
#   - 에러 후 같은 도구가 성공하면 해당 에러를 context에서 제거
#   - 연속 에러 경고를 context에 삽입

from __future__ import annotations
from events import (AgentEvent, TaskStarted, SnapshotRefreshed,
                    LLMResponded, ToolRejected, ToolSucceeded,
                    ToolFailed, HumanAsked, HumanResponded, ContextCompacted,
                    SubAgentStarted, SubAgentCompleted,
                    AgentCompleted, AgentFailed)
# 프롬프트는 BAML(baml_src/agents/*.baml)이 관리.
# 아래 상수는 derive_context() / replay_at() 디버그 재현용으로만 사용.
SYSTEM_PROMPT = "(BAML 관리 — baml_src/agents/*.baml 참조)"

XML_CONTEXT_TEMPLATES = {
    "system":         "<system_instruction>\n{content}\n</system_instruction>",
    "tool_result":    "<tool_result name=\"{name}\">\n{content}\n</tool_result>",
    "tool_error":     "<tool_error name=\"{name}\">\n{error_type}: {message}\n</tool_error>",
    "tool_rejected":  "<tool_rejected name=\"{name}\">\n거부 사유: {reason}\n원래 파라미터: {params}\n</tool_rejected>",
    "human_response": "<human_response>\n{content}\n</human_response>",
    "portfolio":      "<portfolio_snapshot timestamp=\"{timestamp}\">\n{content}\n</portfolio_snapshot>",
    "snapshot_refresh":  "<snapshot_refreshed stale_minutes=\"{stale_minutes}\">\n{content}\n</snapshot_refreshed>",
    "compaction":     "<compacted_summary count=\"{count}\">\n{content}\n</compacted_summary>",
    "step_warning":   "<step_warning current=\"{current}\" max=\"{max}\">\n{message}\n</step_warning>",
    "error_escalation": "<error_escalation consecutive=\"{count}\">\n{message}\n</error_escalation>",
    "sub_agent_result": "<sub_agent_result agent=\"{name}\" status=\"{status}\">\n{content}\n</sub_agent_result>",
}


MAX_CONTEXT_MESSAGES = 20
MAX_ERROR_LEN        = 200


def derive_context(events: list[AgentEvent], context_format: str = "xml") -> list[dict]:
    """
    Factor 12: 핵심 순수 함수.
    이벤트 목록 → LLM API에 넘길 messages 배열.

    Factor 3: context_format 파라미터로 형식 전환 (A/B 테스트 가능)
      - "xml":    XML 태그로 구조화된 형식 (기본값)
      - "plain":  기존 평문 형식
      - "single": 모든 이벤트를 단일 user 메시지로 결합 (원문 Factor 3 패턴)

    이 함수만 있으면:
    - 어느 시점이든 context 재현 가능 (replay)
    - 특정 스텝으로 롤백 = events[:N]으로 호출
    - 브랜치 = 다른 이벤트 붙여서 호출
    """
    # Factor 3: "single" 모드 — 원문의 thread_to_prompt() 패턴
    if context_format == "single":
        return _derive_single_message(events)

    fmt = _xml_formatter if context_format == "xml" else _plain_formatter
    messages: list[dict] = []

    # Factor 9 개선: 해결된 에러 추적 — 성공한 도구의 이전 에러를 제거
    resolved_tools = _find_resolved_errors(events)

    for i, event in enumerate(events):
        match event:

            case TaskStarted(task=task, portfolio_summary=summary):
                messages.append({
                    "role": "user",
                    "content": fmt("portfolio", content=summary,
                                   timestamp="start") + "\n\n" + _task_description(task)
                })

            case SnapshotRefreshed(portfolio_summary=summary, stale_minutes=stale):
                messages.append({
                    "role": "user",
                    "content": fmt("snapshot_refresh", content=summary,
                                   stale_minutes=str(stale))
                })

            case LLMResponded(raw_output=output):
                messages.append({"role": "assistant", "content": output})

            case ToolRejected(tool_name=name, reject_reason=reason, original_params=params):
                messages.append({
                    "role": "user",
                    "content": fmt("tool_rejected", name=name,
                                   reason=reason, params=params)
                })

            case ToolSucceeded(tool_name=name, result=result):
                messages.append({
                    "role": "user",
                    "content": fmt("tool_result", name=name, content=result)
                })

            case ToolFailed(tool_name=name, error_type=etype, error_msg=emsg):
                # Factor 9: 이후에 같은 도구가 성공했으면 이 에러는 건너뛰기
                if i in resolved_tools:
                    continue
                messages.append({
                    "role": "user",
                    "content": fmt("tool_error", name=name,
                                   error_type=etype,
                                   message=emsg[:MAX_ERROR_LEN])
                })

            case HumanAsked():
                pass  # 질문 자체는 context에 불필요 (LLMResponded에 이미 있음)

            case HumanResponded(answer=answer):
                messages.append({
                    "role": "user",
                    "content": fmt("human_response", content=answer)
                })

            case SubAgentStarted(agent_name=name, task=sub_task):
                pass  # Controller의 LLMResponded에 이미 delegate 정보 있음

            case SubAgentCompleted(agent_name=name, status=st, summary=summ):
                # Factor 10: Sub-Agent 결과를 Controller context에 삽입
                messages.append({
                    "role": "user",
                    "content": fmt("sub_agent_result", name=name,
                                   status=st, content=summ)
                })

            case ContextCompacted():
                pass  # 아래 _apply_compaction에서 처리

    # 압축 적용
    messages = _apply_compaction(events, messages, fmt)

    # 시스템 프롬프트 주입 (Role Hacking — system 메시지가 아닌 user/assistant 형태로)
    return [
        {"role": "user",      "content": fmt("system", content=SYSTEM_PROMPT)},
        {"role": "assistant", "content": "네, JSON만 반환하겠습니다."},
    ] + messages


def _find_resolved_errors(events: list[AgentEvent]) -> set[int]:
    """
    Factor 9 개선: 에러 후 같은 도구가 성공하면 해당 에러의 인덱스를 반환.
    해결된 에러는 context에서 제거하여 LLM 혼동 방지.
    """
    resolved = set()
    # 역순으로 성공한 도구 목록 수집
    succeeded_tools = set()
    for i in range(len(events) - 1, -1, -1):
        event = events[i]
        if isinstance(event, ToolSucceeded):
            succeeded_tools.add(event.tool_name)
        # Mark ToolFailed and ToolRejected events as resolved if the same tool later succeeded,
        # so that resolved errors are removed from context and do not confuse the LLM.
        elif (isinstance(event, (ToolFailed, ToolRejected)) and event.tool_name in succeeded_tools):
            resolved.add(i)
    return resolved


def _apply_compaction(events: list[AgentEvent], messages: list[dict],
                      fmt) -> list[dict]:
    """
    ContextCompacted 이벤트가 있으면 그 이전 메시지를 summary로 교체.
    이 로직 덕분에 컨텍스트 압축 이력도 replay 가능.
    """
    compaction_indices = [
        i for i, e in enumerate(events) if isinstance(e, ContextCompacted)
    ]
    if not compaction_indices:
        return messages

    # 마지막 압축 이벤트 기준으로만 처리
    last_compaction = events[compaction_indices[-1]]
    resolved_tools = _find_resolved_errors(events)

    # 압축 이벤트 이후 생성된 메시지만 남기기
    events_after_compaction = events[compaction_indices[-1]+1:]
    messages_after = []
    for j, event in enumerate(events_after_compaction):
        global_idx = compaction_indices[-1] + 1 + j
        match event:
            case LLMResponded(raw_output=o):
                messages_after.append({"role": "assistant", "content": o})
            case ToolSucceeded(tool_name=n, result=r):
                messages_after.append({
                    "role": "user",
                    "content": fmt("tool_result", name=n, content=r)
                })
            case ToolRejected(tool_name=n, reject_reason=reason, original_params=p):
                messages_after.append({
                    "role": "user",
                    "content": fmt("tool_rejected", name=n, reason=reason, params=p)
                })
            case ToolFailed(tool_name=n, error_type=et, error_msg=em):
                if global_idx not in resolved_tools:
                    messages_after.append({
                        "role": "user",
                        "content": fmt("tool_error", name=n,
                                       error_type=et, message=em[:MAX_ERROR_LEN])
                    })
            case HumanResponded(answer=a):
                messages_after.append({
                    "role": "user",
                    "content": fmt("human_response", content=a)
                })
            case SnapshotRefreshed(portfolio_summary=s, stale_minutes=st):
                messages_after.append({
                    "role": "user",
                    "content": fmt("snapshot_refresh", content=s,
                                   stale_minutes=str(st))
                })
            case SubAgentCompleted(agent_name=n, status=st, summary=summ):
                messages_after.append({
                    "role": "user",
                    "content": fmt("sub_agent_result", name=n,
                                   status=st, content=summ)
                })

    return [{
        "role": "user",
        "content": fmt("compaction", content=last_compaction.summary,
                        count=str(last_compaction.compacted_count))
    }] + messages_after


def should_compact(events: list[AgentEvent],
                   max_messages: int = MAX_CONTEXT_MESSAGES) -> bool:
    """컨텍스트 압축이 필요한지 판단 (Factor 10: 컨텍스트 열화 방지)"""
    last_compaction = next(
        (i for i in range(len(events)-1, -1, -1)
         if isinstance(events[i], ContextCompacted)), -1
    )
    events_since = len(events) - last_compaction - 1
    return events_since > max_messages


def make_compaction_event(events: list[AgentEvent]) -> ContextCompacted:
    """압축 이벤트 생성 — 이것도 순수 함수"""
    tool_names = [e.tool_name for e in events if isinstance(e, ToolSucceeded)]
    summary = f"완료된 도구: {', '.join(tool_names[-5:])}" if tool_names else "진행 중"
    return ContextCompacted(
        compacted_count=len(events),
        summary=summary
    )


def count_consecutive_errors(events: list[AgentEvent]) -> int:
    """
    Factor 9: 가장 최근부터 역순으로 연속 에러(ToolFailed + ToolRejected) 수를 세기.
    ToolSucceeded나 다른 이벤트가 나오면 카운트 중단.
    """
    count = 0
    for event in reversed(events):
        if isinstance(event, (ToolFailed, ToolRejected)):
            count += 1
        elif isinstance(event, (ToolSucceeded, TaskStarted, HumanResponded,
                                SnapshotRefreshed)):
            break
        # LLMResponded, HumanAsked 등은 건너뛰기
    return count


def count_steps(events: list[AgentEvent]) -> int:
    """현재까지의 LLM 호출 스텝 수 (Factor 10: 스텝 경고용)"""
    return sum(1 for e in events if isinstance(e, LLMResponded))


def _task_description(task: str) -> str:
    return {
        "daily_report":  "일간 리포트를 생성하고 Notion에 저장해주세요.",
        "alert_check":   "포트폴리오를 점검하고 이상 징후가 있으면 알림을 발송해주세요.",
        "rebalance":     "포트폴리오 리밸런싱 권고안을 분석해주세요.",
        "weekly_report": "주간 종합 리포트를 생성해주세요.",
    }.get(task, task)


# ── 형식 변환 함수들 (Factor 3: Custom Context Format) ─────────────

def _xml_formatter(template_key: str, **kwargs) -> str:
    """XML 태그 형식으로 컨텍스트 항목 생성 (Factor 3)"""
    template = XML_CONTEXT_TEMPLATES.get(template_key)
    if template:
        return template.format(**kwargs)
    # 알 수 없는 키면 평문 폴백
    return _plain_formatter(template_key, **kwargs)


def _plain_formatter(template_key: str, **kwargs) -> str:
    """기존 평문 형식으로 컨텍스트 항목 생성"""
    formatters = {
        "system": lambda: kwargs.get("content", ""),
        "tool_result": lambda: f"[{kwargs.get('name', '')} 결과]\n{kwargs.get('content', '')}",
        "tool_error": lambda: f"[{kwargs.get('name', '')} 에러] {kwargs.get('error_type', '')}: {kwargs.get('message', '')}",
        "tool_rejected": lambda: f"[{kwargs.get('name', '')} 거부] 사유: {kwargs.get('reason', '')} | 파라미터: {kwargs.get('params', '')}",
        "human_response": lambda: f"[사용자 응답] {kwargs.get('content', '')}",
        "portfolio": lambda: kwargs.get("content", ""),
        "snapshot_refresh": lambda: f"[스냅샷 갱신 (이전 {kwargs.get('stale_minutes', '?')}분 경과)]\n{kwargs.get('content', '')}",
        "compaction": lambda: f"[이전 {kwargs.get('count', '?')}개 스텝 요약]\n{kwargs.get('content', '')}",
        "step_warning": lambda: f"[경고] 현재 {kwargs.get('current', '?')}/{kwargs.get('max', '?')} 스텝 — {kwargs.get('message', '')}",
        "error_escalation": lambda: f"[에러 에스컬레이션] 연속 {kwargs.get('count', '?')}회 — {kwargs.get('message', '')}",
        "sub_agent_result": lambda: f"[{kwargs.get('name', '')} 에이전트 결과 ({kwargs.get('status', '')})] {kwargs.get('content', '')}",
    }
    fn = formatters.get(template_key, lambda: str(kwargs))
    return fn()


def _derive_single_message(events: list[AgentEvent]) -> list[dict]:
    """
    Factor 3 원문 패턴: thread_to_prompt().
    모든 이벤트를 XML 태그로 변환하여 단일 user 메시지에 결합.

    원문: "Here's everything that happened so far: ..."
    → 하나의 user 메시지에 모든 컨텍스트를 넣어 LLM의 주의를 집중시킨다.
    """
    resolved = _find_resolved_errors(events)
    parts = [f"<system_instruction>\n{SYSTEM_PROMPT}\n</system_instruction>"]

    for i, event in enumerate(events):
        match event:
            case TaskStarted(task=task, portfolio_summary=summary):
                parts.append(
                    f"<task_started>\n"
                    f"<portfolio>\n{summary}\n</portfolio>\n"
                    f"<task>{_task_description(task)}</task>\n"
                    f"</task_started>"
                )
            case SnapshotRefreshed(portfolio_summary=s, stale_minutes=st):
                parts.append(
                    f"<snapshot_refreshed stale_minutes=\"{st}\">\n{s}\n</snapshot_refreshed>"
                )
            case LLMResponded(raw_output=output):
                parts.append(f"<agent_action>\n{output}\n</agent_action>")
            case ToolSucceeded(tool_name=name, result=result):
                parts.append(f"<tool_result name=\"{name}\">\n{result}\n</tool_result>")
            case ToolFailed(tool_name=name, error_type=etype, error_msg=emsg):
                if i not in resolved:
                    parts.append(
                        f"<tool_error name=\"{name}\">\n{etype}: {emsg[:MAX_ERROR_LEN]}\n</tool_error>"
                    )
            case ToolRejected(tool_name=name, reject_reason=reason, original_params=params):
                parts.append(
                    f"<tool_rejected name=\"{name}\">\n거부 사유: {reason}\n원래 파라미터: {params}\n</tool_rejected>"
                )
            case HumanResponded(answer=answer):
                parts.append(f"<human_response>\n{answer}\n</human_response>")
            case SubAgentCompleted(agent_name=name, status=st, summary=summ):
                parts.append(
                    f"<sub_agent_result agent=\"{name}\" status=\"{st}\">\n{summ}\n</sub_agent_result>"
                )

    parts.append("\nWhat should the next step be?")

    return [{"role": "user", "content": "\n\n".join(parts)}]
