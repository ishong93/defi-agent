# events.py — Factor 12의 핵심: 불변 이벤트 타입
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
from typing import Literal, Union
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
    kind:           Literal["TaskStarted"] = "TaskStarted"
    task:           str = ""
    portfolio_summary: str = ""      # 시작 시점의 포트폴리오 스냅샷 텍스트


@dataclass(frozen=True)
class LLMResponded(BaseEvent):
    """LLM이 다음 툴을 결정함"""
    kind:       Literal["LLMResponded"] = "LLMResponded"
    raw_output: str = ""             # LLM 원본 출력 (JSON)
    tool_name:  str = ""
    tool_params: str = ""            # JSON string
    reason:     str = ""


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
    """에이전트가 사람에게 확인을 요청함 (Factor 7)"""
    kind:     Literal["HumanAsked"] = "HumanAsked"
    level:    str = "info"           # info | warning | critical
    question: str = ""
    context:  str = ""


@dataclass(frozen=True)
class HumanResponded(BaseEvent):
    """사람이 응답함"""
    kind:   Literal["HumanResponded"] = "HumanResponded"
    answer: str = ""


@dataclass(frozen=True)
class ContextCompacted(BaseEvent):
    """컨텍스트가 너무 길어져 오래된 내용을 요약함 (Factor 9)"""
    kind:            Literal["ContextCompacted"] = "ContextCompacted"
    compacted_count: int = 0
    summary:         str = ""


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
    TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
    HumanAsked, HumanResponded, ContextCompacted,
    AgentCompleted, AgentFailed,
]
