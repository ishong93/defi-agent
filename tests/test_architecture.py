# tests/test_architecture.py — Factor 6·12 아키텍처 검증

import pytest
import tempfile
from pathlib import Path
from events import *
from reducer import derive_context, should_compact, make_compaction_event
from event_store import EventStore


# ══════════════════════════════════════════════════════════════════
#  Factor 12: 순수 리듀서 검증
# ══════════════════════════════════════════════════════════════════

class TestPureReducer:

    def test_same_events_always_produce_same_context(self):
        """결정론적 — 같은 이벤트 → 항상 같은 context"""
        events = [
            TaskStarted(task="alert_check", portfolio_summary="총 자산: $10,000"),
            LLMResponded(raw_output='{"tool":"fetch_all_portfolios","params":{}}', tool_name="fetch_all_portfolios"),
            ToolSucceeded(tool_name="fetch_all_portfolios", result="FLR: 1250"),
        ]
        ctx1 = derive_context(events)
        ctx2 = derive_context(events)
        assert ctx1 == ctx2

    def test_derive_context_does_not_mutate_events(self):
        """원본 이벤트 목록 변경 없음"""
        events = [TaskStarted(task="test", portfolio_summary="data")]
        original_len = len(events)
        derive_context(events)
        assert len(events) == original_len

    def test_partial_replay_rebuilds_correct_context(self):
        """
        이벤트 목록의 일부만 사용해도 그 시점의 context 재현.
        이것이 "타임머신" 기능의 핵심.
        """
        all_events = [
            TaskStarted(task="alert_check", portfolio_summary="포트폴리오"),
            LLMResponded(raw_output='{"tool":"fetch_all_portfolios"}', tool_name="fetch_all_portfolios"),
            ToolSucceeded(tool_name="fetch_all_portfolios", result="step1 결과"),
            LLMResponded(raw_output='{"tool":"analyze_portfolio"}', tool_name="analyze_portfolio"),
            ToolSucceeded(tool_name="analyze_portfolio", result="step2 결과"),
        ]
        # 2번째 툴 실행 전 상태 재현
        ctx_at_step1 = derive_context(all_events[:3])
        ctx_at_step2 = derive_context(all_events)

        # step1 context에는 step2 결과 없음
        step1_content = str(ctx_at_step1)
        step2_content = str(ctx_at_step2)
        assert "step1 결과" in step1_content
        assert "step2 결과" not in step1_content
        assert "step2 결과" in step2_content

    def test_tool_failed_event_becomes_compressed_error_in_context(self):
        """ToolFailed 이벤트 → context에서 에러가 압축됨 (Factor 9)"""
        long_error = "x" * 500
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="some_tool"),
            ToolFailed(tool_name="some_tool", error_type="ConnectionError", error_msg=long_error),
        ]
        ctx = derive_context(events)
        # 마지막 메시지에서 에러 확인
        error_msg = next(m for m in ctx if "에러" in m.get("content", ""))
        assert len(error_msg["content"]) < 300  # 압축됨

    def test_human_responded_appears_in_context(self):
        """HumanResponded 이벤트 → context에 사용자 응답 포함"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"ask_human"}', tool_name="ask_human"),
            HumanAsked(level="warning", question="리포트 발송할까요?"),
            HumanResponded(answer="네, 발송하세요"),
        ]
        ctx = derive_context(events)
        human_msg = next(
            (m for m in ctx if "[사용자 응답]" in m.get("content", "")), None
        )
        assert human_msg is not None
        assert "네, 발송하세요" in human_msg["content"]

    def test_context_compaction_replaces_old_messages(self):
        """ContextCompacted 이벤트 → 이전 메시지들이 summary로 대체됨"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"tool_a"}', tool_name="tool_a"),
            ToolSucceeded(tool_name="tool_a", result="결과A — 오래된 데이터"),
            ContextCompacted(compacted_count=3, summary="tool_a 완료됨"),
            LLMResponded(raw_output='{"tool":"tool_b"}', tool_name="tool_b"),
            ToolSucceeded(tool_name="tool_b", result="결과B — 최신 데이터"),
        ]
        ctx = derive_context(events)
        full_content = " ".join(m["content"] for m in ctx)

        assert "tool_a 완료됨" in full_content      # summary 포함
        assert "결과A" not in full_content           # 오래된 원본 제거
        assert "결과B — 최신 데이터" in full_content  # 최신 데이터 유지

    def test_branching_from_same_checkpoint(self):
        """
        같은 이벤트 기반에서 다른 툴 결과를 붙이면 다른 context 생성.
        이것이 브랜치(분기) 기능 — A/B 테스트, 재시도 분기에 활용 가능.
        """
        base_events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"analyze"}', tool_name="analyze"),
        ]
        branch_a = base_events + [ToolSucceeded(tool_name="analyze", result="분석결과_A")]
        branch_b = base_events + [ToolSucceeded(tool_name="analyze", result="분석결과_B")]

        ctx_a = derive_context(branch_a)
        ctx_b = derive_context(branch_b)

        assert ctx_a != ctx_b
        assert "분석결과_A" in str(ctx_a)
        assert "분석결과_B" in str(ctx_b)


# ══════════════════════════════════════════════════════════════════
#  Factor 6: 이벤트 저장소 검증
# ══════════════════════════════════════════════════════════════════

class TestEventStore:

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.store = EventStore(Path(self.tmp.name))
        self.run_id = "test_run_001"
        self.store.start_run(self.run_id, "alert_check")

    def test_append_and_load_preserves_order(self):
        """이벤트 순서가 보존됨"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="fetch"),
            ToolSucceeded(tool_name="fetch", result="OK"),
        ]
        for e in events:
            self.store.append(self.run_id, e)

        loaded = self.store.load(self.run_id)
        assert [type(e).__name__ for e in loaded] == [
            "TaskStarted", "LLMResponded", "ToolSucceeded"
        ]

    def test_load_until_enables_point_in_time_recovery(self):
        """
        load_until(N) → 스텝 N까지만 복원.
        이것이 롤백/타임머신 기능의 핵심.
        """
        events = [
            TaskStarted(task="test", portfolio_summary="p"),
            LLMResponded(raw_output='{}', tool_name="t1"),
            ToolSucceeded(tool_name="t1", result="r1"),
            LLMResponded(raw_output='{}', tool_name="t2"),
            ToolSucceeded(tool_name="t2", result="r2"),
        ]
        for e in events:
            self.store.append(self.run_id, e)

        events_at_step1 = self.store.load_until(self.run_id, seq=2)
        assert len(events_at_step1) == 3  # TaskStarted + LLMResponded + ToolSucceeded
        assert isinstance(events_at_step1[-1], ToolSucceeded)
        assert events_at_step1[-1].result == "r1"

    def test_events_are_truly_immutable(self):
        """이벤트 객체 수정 시도 → TypeError 발생 (frozen=True)"""
        event = TaskStarted(task="original", portfolio_summary="p")
        with pytest.raises((TypeError, AttributeError)):
            event.task = "modified"  # type: ignore

    def test_run_status_reflects_last_event(self):
        """마지막 이벤트 타입에 따라 run 상태가 자동 갱신"""
        self.store.append(self.run_id, TaskStarted(task="test", portfolio_summary="p"))
        assert self.store.get_run_status(self.run_id) == "running"

        self.store.append(self.run_id, HumanAsked(question="확인?"))
        assert self.store.get_run_status(self.run_id) == "paused"

        self.store.append(self.run_id, HumanResponded(answer="확인"))
        assert self.store.get_run_status(self.run_id) == "running"

        self.store.append(self.run_id, AgentCompleted(summary="완료"))
        assert self.store.get_run_status(self.run_id) == "done"

    def test_resumable_list_excludes_completed_runs(self):
        """완료된 실행은 재개 목록에서 제외"""
        self.store.start_run("done_run", "test")
        self.store.append("done_run", AgentCompleted(summary="끝"))

        resumable = self.store.list_resumable()
        ids = [r["run_id"] for r in resumable]
        assert self.run_id in ids
        assert "done_run" not in ids

    def test_event_count_is_accurate(self):
        events = [
            TaskStarted(task="t", portfolio_summary="p"),
            LLMResponded(raw_output='{}', tool_name="x"),
        ]
        for e in events:
            self.store.append(self.run_id, e)
        assert self.store.count_events(self.run_id) == 2
