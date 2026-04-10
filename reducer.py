# reducer.py — Factor 12: Agent as a Stateless Reducer
#
# 핵심 원리:
#   derive_context(events) → LLM에 넘길 messages 배열
#
#   이 함수는 완전한 순수 함수다:
#   - 사이드 이펙트 없음
#   - 같은 이벤트 목록 → 항상 같은 context
#   - 테스트하기 매우 쉬움
#   - 이벤트 목록만 있으면 어느 시점이든 context 재현 가능
#
# Redux의 reducer와 정확히 같은 패턴:
#   Redux:  (state, action) → state
#   여기:   (events)        → context (messages[])

from __future__ import annotations
from events import (AgentEvent, TaskStarted, LLMResponded, ToolSucceeded,
                    ToolFailed, HumanAsked, HumanResponded, ContextCompacted,
                    AgentCompleted, AgentFailed)
from prompts import SYSTEM_PROMPT


MAX_CONTEXT_MESSAGES = 20
MAX_ERROR_LEN        = 200


def derive_context(events: list[AgentEvent]) -> list[dict]:
    """
    Factor 12: 핵심 순수 함수.
    이벤트 목록 → LLM API에 넘길 messages 배열.

    이 함수만 있으면:
    - 어느 시점이든 context 재현 가능 (replay)
    - 특정 스텝으로 롤백 = events[:N]으로 호출
    - 브랜치 = 다른 이벤트 붙여서 호출
    """
    messages: list[dict] = []

    for event in events:
        match event:

            case TaskStarted(task=task, portfolio_summary=summary):
                messages.append({
                    "role": "user",
                    "content": f"{summary}\n\n{_task_description(task)}"
                })

            case LLMResponded(raw_output=output):
                messages.append({"role": "assistant", "content": output})

            case ToolSucceeded(tool_name=name, result=result):
                messages.append({
                    "role": "user",
                    "content": f"[{name} 결과]\n{result}"
                })

            case ToolFailed(tool_name=name, error_type=etype, error_msg=emsg):
                # Factor 9: 에러 압축
                messages.append({
                    "role": "user",
                    "content": f"[{name} 에러] {etype}: {emsg[:MAX_ERROR_LEN]}"
                })

            case HumanAsked():
                pass  # 질문 자체는 context에 불필요 (LLMResponded에 이미 있음)

            case HumanResponded(answer=answer):
                messages.append({
                    "role": "user",
                    "content": f"[사용자 응답] {answer}"
                })

            case ContextCompacted(compacted_count=n, summary=summary):
                # 압축 지점 — 이 이벤트 이전 메시지들은 summary로 대체됨
                # (아래 _apply_compaction에서 처리)
                pass

    # 압축 적용
    messages = _apply_compaction(events, messages)

    # 시스템 프롬프트 주입 (항상 맨 앞)
    return [
        {"role": "user",      "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": "네, JSON만 반환하겠습니다."},
    ] + messages


def _apply_compaction(events: list[AgentEvent], messages: list[dict]) -> list[dict]:
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
    # 압축 이벤트 이후 생성된 메시지만 남기기
    events_after_compaction = events[compaction_indices[-1]+1:]
    messages_after = []
    for event in events_after_compaction:
        match event:
            case LLMResponded(raw_output=o):
                messages_after.append({"role": "assistant", "content": o})
            case ToolSucceeded(tool_name=n, result=r):
                messages_after.append({"role": "user", "content": f"[{n} 결과]\n{r}"})
            case ToolFailed(tool_name=n, error_type=et, error_msg=em):
                messages_after.append({"role": "user", "content": f"[{n} 에러] {et}: {em[:MAX_ERROR_LEN]}"})
            case HumanResponded(answer=a):
                messages_after.append({"role": "user", "content": f"[사용자 응답] {a}"})

    return [{"role": "user",
             "content": f"[이전 {last_compaction.compacted_count}개 스텝 요약]\n{last_compaction.summary}"}
            ] + messages_after


def should_compact(events: list[AgentEvent]) -> bool:
    """컨텍스트 압축이 필요한지 판단"""
    # 마지막 압축 이후의 이벤트 수로 판단
    last_compaction = next(
        (i for i in range(len(events)-1, -1, -1)
         if isinstance(events[i], ContextCompacted)), -1
    )
    events_since = len(events) - last_compaction - 1
    return events_since > MAX_CONTEXT_MESSAGES


def make_compaction_event(events: list[AgentEvent]) -> ContextCompacted:
    """압축 이벤트 생성 — 이것도 순수 함수"""
    tool_names = [e.tool_name for e in events if isinstance(e, ToolSucceeded)]
    summary = f"완료된 툴: {', '.join(tool_names[-5:])}" if tool_names else "진행 중"
    return ContextCompacted(
        compacted_count=len(events),
        summary=summary
    )


def _task_description(task: str) -> str:
    return {
        "daily_report":  "일간 리포트를 생성하고 Notion에 저장해주세요.",
        "alert_check":   "포트폴리오를 점검하고 이상 징후가 있으면 알림을 발송해주세요.",
        "rebalance":     "포트폴리오 리밸런싱 권고안을 분석해주세요.",
        "weekly_report": "주간 종합 리포트를 생성해주세요.",
    }.get(task, task)
