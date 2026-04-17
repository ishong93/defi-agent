# tests/test_performance.py — Phase 1 최적화의 성능/정확성 회귀 방지 테스트
#
# 목표:
#   1. EventStore.append_batch() 가 append() 대비 빠르고 동등한 결과를 낸다
#   2. WAL 모드가 활성화되어 있다
#   3. derive_context()가 큰 이벤트 목록에서도 합리적인 시간 내 처리한다
#   4. make_anthropic_llm(enable_prompt_cache=True) 가 프리픽스에만
#      cache_control을 적용한다 (API 호출은 모킹)

import time
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from events import (TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
                    ContextCompacted)
from event_store import EventStore
from reducer import derive_context


# ══════════════════════════════════════════════════════════════════
#  EventStore 최적화 검증
# ══════════════════════════════════════════════════════════════════

class TestEventStoreOptimizations:

    def setup_method(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.store = EventStore(Path(self.tmp.name))
        self.store.start_run("perf_run", "test")

    def test_wal_mode_enabled(self):
        """WAL 모드가 활성화되어야 쓰기 성능이 향상됨"""
        mode = self.store.conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal", f"WAL 아님: {mode}"

    def test_append_batch_equivalent_to_sequential_append(self):
        """배치 저장과 순차 저장의 결과가 동일해야 함"""
        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{}', tool_name="a"),
            ToolSucceeded(tool_name="a", result="ok"),
            LLMResponded(raw_output='{}', tool_name="b"),
            ToolSucceeded(tool_name="b", result="ok"),
        ]

        # 다른 store에 순차 저장
        tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        store2 = EventStore(Path(tmp2.name))
        store2.start_run("perf_run", "test")
        for e in events:
            store2.append("perf_run", e)

        # 배치 저장
        seqs = self.store.append_batch("perf_run", events)

        assert seqs == [0, 1, 2, 3, 4]
        assert self.store.count_events("perf_run") == store2.count_events("perf_run")

        loaded_batch = [type(e).__name__ for e in self.store.load("perf_run")]
        loaded_seq = [type(e).__name__ for e in store2.load("perf_run")]
        assert loaded_batch == loaded_seq

    def test_append_batch_empty_list_is_noop(self):
        """빈 리스트 전달 시 아무 변화 없음"""
        result = self.store.append_batch("perf_run", [])
        assert result == []
        assert self.store.count_events("perf_run") == 0

    def test_append_batch_faster_than_sequential(self):
        """100개 이벤트 저장: 배치가 순차보다 빨라야 함"""
        events = [
            ToolSucceeded(tool_name=f"t{i}", result=f"r{i}") for i in range(100)
        ]

        tmp_seq = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        store_seq = EventStore(Path(tmp_seq.name))
        store_seq.start_run("r", "t")
        t0 = time.perf_counter()
        for e in events:
            store_seq.append("r", e)
        seq_time = time.perf_counter() - t0

        tmp_batch = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        store_batch = EventStore(Path(tmp_batch.name))
        store_batch.start_run("r", "t")
        t0 = time.perf_counter()
        store_batch.append_batch("r", events)
        batch_time = time.perf_counter() - t0

        assert batch_time < seq_time, (
            f"batch={batch_time*1000:.1f}ms seq={seq_time*1000:.1f}ms"
        )

    def test_seq_cache_consistent_with_db(self):
        """인메모리 seq 캐시가 DB 조회 결과와 일치해야 함"""
        events = [
            TaskStarted(task="t", portfolio_summary="p"),
            LLMResponded(raw_output='{}', tool_name="x"),
        ]
        for e in events:
            self.store.append("perf_run", e)

        # 캐시 우회해서 DB에서 직접 확인
        max_seq = self.store.conn.execute(
            "SELECT MAX(seq) FROM events WHERE run_id=?", ("perf_run",)
        ).fetchone()[0]
        assert self.store._last_seq["perf_run"] == max_seq


# ══════════════════════════════════════════════════════════════════
#  Reducer 성능 — O(n) 기대치 검증
# ══════════════════════════════════════════════════════════════════

class TestReducerPerformance:

    def test_derive_context_scales_linearly(self):
        """1000 이벤트 처리가 1초 이내 — 한 스텝 내 블로킹이 허용 범위"""
        events = [TaskStarted(task="test", portfolio_summary="data")]
        for i in range(500):
            events.append(LLMResponded(
                raw_output=f'{{"tool":"t{i}"}}', tool_name=f"t{i}"
            ))
            events.append(ToolSucceeded(tool_name=f"t{i}", result=f"r{i}"))

        t0 = time.perf_counter()
        ctx = derive_context(events)
        elapsed = time.perf_counter() - t0

        assert len(ctx) > 0
        assert elapsed < 1.0, f"1001 이벤트 처리에 {elapsed*1000:.1f}ms"

    def test_derive_context_with_compaction_is_bounded(self):
        """ContextCompacted 이후의 이벤트만 처리되므로 압축 후 출력 크기 제한"""
        events = [TaskStarted(task="test", portfolio_summary="data")]
        for i in range(50):
            events.append(LLMResponded(raw_output='{}', tool_name=f"t{i}"))
            events.append(ToolSucceeded(tool_name=f"t{i}", result=f"r{i}"))
        events.append(ContextCompacted(compacted_count=100, summary="요약"))
        # 압축 후 5개만
        for i in range(5):
            events.append(LLMResponded(raw_output='{}', tool_name=f"u{i}"))
            events.append(ToolSucceeded(tool_name=f"u{i}", result=f"s{i}"))

        ctx = derive_context(events)
        # system + assistant_ack + compaction_summary + 10 new messages
        assert 10 < len(ctx) < 20


# ══════════════════════════════════════════════════════════════════
#  Prompt Caching (make_anthropic_llm)
# ══════════════════════════════════════════════════════════════════

class TestPromptCaching:

    def test_cache_control_applied_to_first_message(self):
        """enable_prompt_cache=True: 첫 메시지에 cache_control 블록이 붙는다"""
        from loop import make_anthropic_llm

        captured = {}
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]

        with patch("loop.anthropic.Anthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            def _create(**kwargs):
                captured.update(kwargs)
                return mock_response
            mock_client.messages.create.side_effect = _create

            llm = make_anthropic_llm("claude-sonnet-4-20250514",
                                     enable_prompt_cache=True)
            messages = [
                {"role": "user", "content": "SYSTEM PROMPT"},
                {"role": "assistant", "content": "ack"},
                {"role": "user", "content": "step 1"},
            ]
            llm(messages)

        sent = captured["messages"]
        # 첫 메시지의 content는 list 형태 + cache_control
        assert isinstance(sent[0]["content"], list)
        assert sent[0]["content"][0]["cache_control"] == {"type": "ephemeral"}
        assert sent[0]["content"][0]["text"] == "SYSTEM PROMPT"
        # 나머지는 그대로 string
        assert sent[1]["content"] == "ack"
        assert sent[2]["content"] == "step 1"

    def test_cache_disabled_leaves_messages_unchanged(self):
        """enable_prompt_cache=False: 메시지 구조 변경 없음"""
        from loop import make_anthropic_llm

        captured = {}
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="ok")]

        with patch("loop.anthropic.Anthropic") as mock_client_cls:
            mock_client = mock_client_cls.return_value
            def _create(**kwargs):
                captured.update(kwargs)
                return mock_response
            mock_client.messages.create.side_effect = _create

            llm = make_anthropic_llm("claude-sonnet-4-20250514",
                                     enable_prompt_cache=False)
            messages = [
                {"role": "user", "content": "SYSTEM"},
                {"role": "user", "content": "step"},
            ]
            llm(messages)

        sent = captured["messages"]
        assert sent[0]["content"] == "SYSTEM"
        assert sent[1]["content"] == "step"
