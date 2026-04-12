# events.py — Factor 12: Stateless Reducer의 핵심 — 불변 이벤트 타입
#
# 핵심 아이디어:
#   에이전트에서 "일어난 일"은 모두 이벤트다.
#   이벤트는 과거형이고, 불변이며, append-only다.
#   context는 이벤트들을 재생(replay)해서 언제든 재구성 가능하다.
#
#   Git 커밋과 같다 — 커밋(이벤트)은 삭제 못하고,
#   HEAD(context)는 커밋을 replay해서 만들어진다.

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Union, Optional
from datetime import datetime, timezone
import uuid


# ── 기반 클래스 ────────────────────────────────────────────────────

@dataclass(frozen=True)   # frozen=True → 불변 (실수로 수정 불가)
class BaseEvent:
    event_id:  str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── 이벤트 종류 (과거형 명명) ──────────────────────────────────────

@dataclass(frozen=True)
class TaskStarted(BaseEvent):
    """에이전트 실행이 시작됨"""
    kind:              Literal["TaskStarted"] = "TaskStarted"
    task:              str = ""
    portfolio_summary: str = ""      # 시작 시점의 포트폴리오 스냅샷 텍스트


@dataclass(frozen=True)
class SnapshotRefreshed(BaseEvent):
    """
    포트폴리오 스냅샷이 갱신됨 (Factor 6: Resume 시 신선도 보장).
    재개 시 오래된 스냅샷을 새 데이터로 교체한 사실을 기록.
    """
    kind:              Literal["SnapshotRefreshed"] = "SnapshotRefreshed"
    portfolio_summary: str = ""
    stale_minutes:     int = 0       # 이전 스냅샷이 얼마나 오래됐었는지


@dataclass(frozen=True)
class LLMResponded(BaseEvent):
    """LLM이 다음 툴을 결정함 (Factor 4: 이것은 '제안'이지 '명령'이 아님)"""
    kind:        Literal["LLMResponded"] = "LLMResponded"
    raw_output:  str = ""             # LLM 원본 출력 (JSON)
    tool_name:   str = ""
    tool_params: str = ""             # JSON string
    reason:      str = ""


@dataclass(frozen=True)
class ToolRejected(BaseEvent):
    """
    Factor 4: 도구 호출이 검증 단계에서 거부됨.
    LLM이 제안한 도구 호출이 사전 검증에서 통과하지 못한 경우.
    (예: 금액 한도 초과, 슬리피지 위험, 드라이런 실패)
    """
    kind:        Literal["ToolRejected"] = "ToolRejected"
    tool_name:   str = ""
    reject_reason: str = ""           # 왜 거부됐는지
    original_params: str = ""         # 거부된 원래 파라미터 (JSON)


@dataclass(frozen=True)
class ToolSucceeded(BaseEvent):
    """툴이 성공적으로 실행됨"""
    kind:      Literal["ToolSucceeded"] = "ToolSucceeded"
    tool_name: str = ""
    result:    str = ""


@dataclass(frozen=True)
class ToolFailed(BaseEvent):
    """툴 실행이 실패함 (Factor 9: 에러 압축)"""
    kind:       Literal["ToolFailed"] = "ToolFailed"
    tool_name:  str = ""
    error_type: str = ""
    error_msg:  str = ""             # 최대 200자로 압축됨


@dataclass(frozen=True)
class HumanAsked(BaseEvent):
    """
    에이전트가 사람에게 확인을 요청함 (Factor 7: Outer Loop).
    Factor 7 원문: RequestHumanInput에 urgency, format 포함.
    """
    kind:            Literal["HumanAsked"] = "HumanAsked"
    level:           str = "info"           # info | warning | critical
    question:        str = ""
    context:         str = ""
    urgency:         str = "medium"         # low | medium | high
    response_format: str = "free_text"      # free_text | yes_no | multiple_choice


@dataclass(frozen=True)
class HumanResponded(BaseEvent):
    """사람이 응답함 (Factor 7: 감사 추적을 위한 approver 필드 포함)"""
    kind:     Literal["HumanResponded"] = "HumanResponded"
    answer:   str = ""
    approver: Optional[str] = None   # 누가 승인했는지 (감사 추적)


@dataclass(frozen=True)
class ContextCompacted(BaseEvent):
    """컨텍스트가 너무 길어져 오래된 내용을 요약함 (Factor 10: 컨텍스트 열화 방지)"""
    kind:            Literal["ContextCompacted"] = "ContextCompacted"
    compacted_count: int = 0
    summary:         str = ""


@dataclass(frozen=True)
class SubAgentStarted(BaseEvent):
    """
    Factor 10 + Factor 7: Controller가 Sub-Agent에게 작업을 위임함.
    Agent→Agent 통신 = Agent→Human과 동일한 패턴.
    """
    kind:       Literal["SubAgentStarted"] = "SubAgentStarted"
    agent_name: str = ""
    task:       str = ""


@dataclass(frozen=True)
class SubAgentCompleted(BaseEvent):
    """
    Factor 10: Sub-Agent가 결과를 반환함.
    Controller는 이 결과를 바탕으로 다음 행동을 결정.
    """
    kind:       Literal["SubAgentCompleted"] = "SubAgentCompleted"
    agent_name: str = ""
    status:     str = ""             # "done" | "failed" | "max_steps_exceeded"
    summary:    str = ""
    sub_run_id: str = ""             # Sub-Agent의 독립 run_id (추적용)


@dataclass(frozen=True)
class AgentCompleted(BaseEvent):
    """에이전트가 정상 완료됨"""
    kind:    Literal["AgentCompleted"] = "AgentCompleted"
    summary: str = ""


@dataclass(frozen=True)
class AgentFailed(BaseEvent):
    """에이전트가 실패함"""
    kind:  Literal["AgentFailed"] = "AgentFailed"
    error: str = ""


# 유니온 타입 — 모든 가능한 이벤트
AgentEvent = Union[
    TaskStarted, SnapshotRefreshed,
    LLMResponded, ToolRejected, ToolSucceeded, ToolFailed,
    HumanAsked, HumanResponded, ContextCompacted,
    SubAgentStarted, SubAgentCompleted,
    AgentCompleted, AgentFailed,
]
