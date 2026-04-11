# tests/test_architecture.py — Factor 3·4·6·9·10·12 아키텍처 검증

import pytest
import tempfile
from pathlib import Path
from events import *
from reducer import (derive_context, should_compact, make_compaction_event,
                     count_consecutive_errors, count_steps)
from tools import validate_tool_call, ValidationResult
from event_store import EventStore
from config import AgentConfig


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
        """이벤트 목록의 일부만 사용해도 그 시점의 context 재현 (타임머신)."""
        all_events = [
            TaskStarted(task="alert_check", portfolio_summary="포트폴리오"),
            LLMResponded(raw_output='{"tool":"fetch_all_portfolios"}', tool_name="fetch_all_portfolios"),
            ToolSucceeded(tool_name="fetch_all_portfolios", result="step1 결과"),
            LLMResponded(raw_output='{"tool":"analyze_portfolio"}', tool_name="analyze_portfolio"),
            ToolSucceeded(tool_name="analyze_portfolio", result="step2 결과"),
        ]
        ctx_at_step1 = derive_context(all_events[:3])
        ctx_at_step2 = derive_context(all_events)

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
        # tool_error 태그가 있는 메시지를 찾되, system_instruction은 제외
        error_msg = next(m for m in ctx
                         if "tool_error" in m.get("content", "")
                         and "system_instruction" not in m.get("content", ""))
        assert len(error_msg["content"]) < 350  # 압축됨 (XML 태그 포함)
        assert "ConnectionError" in error_msg["content"]

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
            (m for m in ctx if "네, 발송하세요" in m.get("content", "")), None
        )
        assert human_msg is not None

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

        assert "tool_a 완료됨" in full_content
        assert "결과A" not in full_content
        assert "결과B" in full_content

    def test_branching_from_same_checkpoint(self):
        """같은 이벤트 기반에서 다른 결과를 붙이면 다른 context 생성 (분기)."""
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
#  Factor 3: Custom Context Format (XML vs Plain)
# ══════════════════════════════════════════════════════════════════

class TestCustomContextFormat:

    def test_xml_format_uses_xml_tags(self):
        """XML 형식이 XML 태그를 사용하는지 확인"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"fetch"}', tool_name="fetch"),
            ToolSucceeded(tool_name="fetch", result="OK"),
        ]
        ctx = derive_context(events, context_format="xml")
        full = " ".join(m["content"] for m in ctx)
        assert "<tool_result" in full
        assert "<system_instruction>" in full

    def test_plain_format_uses_brackets(self):
        """평문 형식이 대괄호 형식을 사용하는지 확인"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"fetch"}', tool_name="fetch"),
            ToolSucceeded(tool_name="fetch", result="OK"),
        ]
        ctx = derive_context(events, context_format="plain")
        full = " ".join(m["content"] for m in ctx)
        assert "[fetch 결과]" in full
        assert "<tool_result" not in full

    def test_both_formats_produce_same_message_count(self):
        """형식만 다를 뿐 메시지 수는 동일"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="t"),
            ToolSucceeded(tool_name="t", result="r"),
        ]
        xml_ctx   = derive_context(events, context_format="xml")
        plain_ctx = derive_context(events, context_format="plain")
        assert len(xml_ctx) == len(plain_ctx)


# ══════════════════════════════════════════════════════════════════
#  Factor 9: 에러 처리 개선 — 해결된 에러 제거 + 연속 에러 카운터
# ══════════════════════════════════════════════════════════════════

class TestErrorHandling:

    def test_resolved_errors_removed_from_context(self):
        """같은 도구가 성공하면 이전 에러가 context에서 제거됨"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"fetch"}', tool_name="fetch"),
            ToolFailed(tool_name="fetch", error_type="Timeout", error_msg="timeout"),
            LLMResponded(raw_output='{"tool":"fetch"}', tool_name="fetch"),
            ToolSucceeded(tool_name="fetch", result="OK"),
        ]
        ctx = derive_context(events)
        full = " ".join(m["content"] for m in ctx)
        assert "timeout" not in full.lower() or "Timeout" not in full
        assert "OK" in full

    def test_unresolved_errors_remain_in_context(self):
        """해결되지 않은 에러는 context에 남아있음"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"fetch"}', tool_name="fetch"),
            ToolFailed(tool_name="fetch", error_type="Timeout", error_msg="timed out"),
        ]
        ctx = derive_context(events)
        full = " ".join(m["content"] for m in ctx)
        assert "timed out" in full

    def test_consecutive_error_counter(self):
        """연속 에러 카운터가 정확하게 동작"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="a"),
            ToolFailed(tool_name="a", error_type="E", error_msg="e1"),
            LLMResponded(raw_output='{}', tool_name="b"),
            ToolFailed(tool_name="b", error_type="E", error_msg="e2"),
            LLMResponded(raw_output='{}', tool_name="c"),
            ToolFailed(tool_name="c", error_type="E", error_msg="e3"),
        ]
        assert count_consecutive_errors(events) == 3

    def test_consecutive_errors_reset_on_success(self):
        """성공하면 연속 에러 카운터가 리셋됨"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="a"),
            ToolFailed(tool_name="a", error_type="E", error_msg="e1"),
            LLMResponded(raw_output='{}', tool_name="b"),
            ToolSucceeded(tool_name="b", result="ok"),
            LLMResponded(raw_output='{}', tool_name="c"),
            ToolFailed(tool_name="c", error_type="E", error_msg="e2"),
        ]
        assert count_consecutive_errors(events) == 1

    def test_step_counter(self):
        """스텝 카운터가 LLMResponded 이벤트 수를 정확히 세기"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="a"),
            ToolSucceeded(tool_name="a", result="r"),
            LLMResponded(raw_output='{}', tool_name="b"),
            ToolSucceeded(tool_name="b", result="r"),
        ]
        assert count_steps(events) == 2


# ══════════════════════════════════════════════════════════════════
#  Factor 4: 도구 호출 = 제안 (검증/거부 테스트)
# ══════════════════════════════════════════════════════════════════

class TestToolValidation:

    def test_done_always_approved(self):
        """done은 항상 승인"""
        result = validate_tool_call({"tool": "done", "params": {}}, AgentConfig())
        assert result.approved is True

    def test_ask_human_always_approved(self):
        """ask_human은 항상 승인"""
        result = validate_tool_call({"tool": "ask_human", "params": {}}, AgentConfig())
        assert result.approved is True

    def test_amount_exceeding_limit_rejected(self):
        """금액 한도 초과 시 거부"""
        config = AgentConfig()
        config.tool_validation.max_transfer_usd = 1000.0
        result = validate_tool_call(
            {"tool": "transfer", "params": {"amount_usd": 5000}}, config
        )
        assert result.approved is False
        assert "한도 초과" in result.reject_reason

    def test_slippage_exceeding_limit_rejected(self):
        """슬리피지 초과 시 거부"""
        config = AgentConfig()
        config.tool_validation.max_slippage_pct = 2.0
        result = validate_tool_call(
            {"tool": "swap", "params": {"slippage_pct": 5.0}}, config
        )
        assert result.approved is False
        assert "슬리피지" in result.reject_reason

    def test_large_amount_requires_human(self):
        """일정 금액 이상은 사람 확인 필요"""
        config = AgentConfig()
        config.tool_validation.require_human_above_usd = 3000.0
        config.tool_validation.max_transfer_usd = 50000.0
        result = validate_tool_call(
            {"tool": "transfer", "params": {"amount_usd": 4000}}, config
        )
        assert result.approved is False
        assert result.requires_human is True

    def test_tool_rejected_event_in_context(self):
        """ToolRejected 이벤트가 context에 올바르게 포함됨"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"transfer"}', tool_name="transfer"),
            ToolRejected(tool_name="transfer", reject_reason="금액 한도 초과",
                         original_params='{"amount_usd": 50000}'),
        ]
        ctx = derive_context(events)
        full = " ".join(m["content"] for m in ctx)
        assert "한도 초과" in full


# ══════════════════════════════════════════════════════════════════
#  Factor 6: 이벤트 저장소 + SnapshotRefreshed
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
        """load_until(N) → 스텝 N까지만 복원 (타임머신)."""
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
        assert len(events_at_step1) == 3
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

    def test_new_event_types_stored_and_loaded(self):
        """새로운 이벤트 타입 (SnapshotRefreshed, ToolRejected)이 정상 저장/로드"""
        events = [
            TaskStarted(task="test", portfolio_summary="p"),
            SnapshotRefreshed(portfolio_summary="new data", stale_minutes=45),
            LLMResponded(raw_output='{}', tool_name="transfer"),
            ToolRejected(tool_name="transfer", reject_reason="한도 초과",
                         original_params='{"amount": 99999}'),
        ]
        for e in events:
            self.store.append(self.run_id, e)

        loaded = self.store.load(self.run_id)
        types = [type(e).__name__ for e in loaded]
        assert "SnapshotRefreshed" in types
        assert "ToolRejected" in types
        assert loaded[1].stale_minutes == 45
        assert loaded[3].reject_reason == "한도 초과"

    def test_human_responded_with_approver(self):
        """HumanResponded에 approver 필드가 저장/로드됨"""
        event = HumanResponded(answer="승인", approver="admin@company.com")
        self.store.append(self.run_id, event)
        loaded = self.store.load(self.run_id)
        assert loaded[0].answer == "승인"
        assert loaded[0].approver == "admin@company.com"
