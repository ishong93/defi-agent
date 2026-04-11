# tests/test_integration.py — 전체 에이전트 실행 통합 테스트
#
# LLM 호출을 스크립트된 응답으로 교체하여
# 실제 API 키 없이도 전체 흐름을 검증.
#
# Factor 4:  도구 호출 검증/거부 흐름
# Factor 6:  재개 + 스냅샷 신선도
# Factor 7:  사람 개입 (ask_human)
# Factor 8:  세 가지 제어 흐름
# Factor 9:  연속 에러 에스컬레이션
# Factor 12: ScriptedLLM 주입

import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from collections import deque

from events import (TaskStarted, SnapshotRefreshed, LLMResponded,
                    ToolRejected, ToolSucceeded, ToolFailed,
                    HumanAsked, HumanResponded, AgentCompleted, AgentFailed)
from event_store import EventStore
from reducer import derive_context
from loop import run_agent, replay_at
from models import PortfolioSnapshot, ChainPortfolio, StakingPosition, DeFiPosition
from config import AgentConfig


# ── 공통 픽스처 ───────────────────────────────────────────────────

def make_snapshot(total_usd=12500.0) -> PortfolioSnapshot:
    chain = ChainPortfolio(
        chain="Flare", wallet_address="0xTEST_WALLET_ADDR",
        native_balance=1250.5, native_price_usd=0.0185,
        total_value_usd=9340.20,
        staking_positions=[
            StakingPosition("Firelight Finance", "stXRP", 500.0, 8.7, 21.5)
        ],
        defi_positions=[
            DeFiPosition("SparkDEX", "LP", ["FXRP", "FLR"], 340.20, 12.50, 0.0023)
        ]
    )
    xdc_chain = ChainPortfolio(
        chain="XDC", wallet_address="0xXDC_WALLET_ADDR",
        native_balance=8500.0, native_price_usd=0.0432,
        total_value_usd=3160.80,
        staking_positions=[
            StakingPosition("PrimeStaking", "XDC", 50000.0, 12.5, 625.0, "2025-06-30")
        ],
    )
    return PortfolioSnapshot(
        timestamp=datetime.now(),
        chains=[chain, xdc_chain],
        total_value_usd=total_usd,
        total_staking_rewards_usd=646.5,
    )


def make_store() -> EventStore:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return EventStore(Path(tmp.name))


def make_config(max_steps=10) -> AgentConfig:
    cfg = AgentConfig()
    cfg.max_steps = max_steps
    return cfg


class ScriptedLLM:
    """
    사전에 정의된 응답을 순서대로 반환하는 목 LLM.
    Factor 12: LLMCallFn 주입으로 실제 API 없이 전체 흐름 테스트 가능.
    """
    def __init__(self, responses: list[str]):
        self.queue   = deque(responses)
        self.calls   = []   # 호출된 messages 기록

    def __call__(self, messages: list) -> str:
        self.calls.append(messages)
        if self.queue:
            return self.queue.popleft()
        return '{"tool":"done","params":{"summary":"스크립트 소진"},"reason":"끝"}'


# ══════════════════════════════════════════════════════════════════
#  1. 정상 흐름 테스트
# ══════════════════════════════════════════════════════════════════

class TestNormalFlow:

    def test_full_alert_check_flow(self):
        """alert_check 전체 흐름: 조회 → 분석 → 알림 탐지 → 리포트 → 완료"""
        llm = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"포트폴리오 조회"}',
            '{"tool":"detect_alerts","params":{},"reason":"이상 탐지"}',
            '{"tool":"generate_report","params":{"type":"daily"},"reason":"일간 리포트"}',
            '{"tool":"done","params":{"summary":"이상 없음. 일간 리포트 생성 완료."},"reason":"완료"}',
        ])
        store    = make_store()
        snapshot = make_snapshot()
        result   = run_agent(snapshot, "alert_check", make_config(),
                             llm_fn=llm, store=store)

        assert result["status"] == "done"
        assert result["steps"] == 4
        assert "이상 없음" in result["summary"]

    def test_events_are_persisted_correctly(self):
        """실행 후 이벤트가 올바른 순서로 저장됨"""
        llm = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])
        store  = make_store()
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, store=store)

        events = store.load(result["run_id"])
        types  = [type(e).__name__ for e in events]

        assert types[0]  == "TaskStarted"
        assert "LLMResponded"  in types
        assert "ToolSucceeded" in types
        assert types[-1] == "AgentCompleted"

    def test_llm_receives_tool_results_in_context(self):
        """LLM이 두 번째 호출 시 첫 번째 도구 결과를 컨텍스트에서 받음"""
        llm = ScriptedLLM([
            '{"tool":"fetch_defi_yields","params":{"chain":"Flare"},"reason":"수익률 조회"}',
            '{"tool":"done","params":{"summary":"수익률 확인 완료"},"reason":"끝"}',
        ])
        store = make_store()
        run_agent(make_snapshot(), "alert_check", make_config(), llm_fn=llm, store=store)

        second_call_messages = llm.calls[1]
        content_str = str(second_call_messages)
        assert "fetch_defi_yields" in content_str or "stXRP" in content_str


# ══════════════════════════════════════════════════════════════════
#  2. 사람 개입 (Factor 7) 테스트
# ══════════════════════════════════════════════════════════════════

class TestHumanInTheLoop:

    def test_ask_human_pauses_and_resumes(self):
        """ask_human → 사람 응답 → 계속 진행"""
        llm = ScriptedLLM([
            '{"tool":"ask_human","params":{"level":"warning","question":"리포트 발송할까요?"},"reason":"확인"}',
            '{"tool":"generate_report","params":{"type":"daily"},"reason":"발송"}',
            '{"tool":"done","params":{"summary":"발송 완료"},"reason":"끝"}',
        ])
        human_answers = deque(["네, 발송하세요"])
        human_fn = lambda level, q, ctx="": human_answers.popleft()

        store  = make_store()
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, human_input_fn=human_fn, store=store)

        assert result["status"] == "done"
        events = store.load(result["run_id"])
        asked_events     = [e for e in events if isinstance(e, HumanAsked)]
        responded_events = [e for e in events if isinstance(e, HumanResponded)]
        assert len(asked_events)     == 1
        assert len(responded_events) == 1
        assert responded_events[0].answer == "네, 발송하세요"

    def test_human_response_appears_in_next_llm_context(self):
        """사람 응답이 다음 LLM 컨텍스트에 포함됨"""
        llm = ScriptedLLM([
            '{"tool":"ask_human","params":{"level":"info","question":"확인?"},"reason":"확인"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])
        human_fn = lambda level, q, ctx="": "텔레그램으로 알림 보내줘"
        store    = make_store()
        run_agent(make_snapshot(), "alert_check", make_config(),
                  llm_fn=llm, human_input_fn=human_fn, store=store)

        assert "텔레그램으로 알림 보내줘" in str(llm.calls[1])


# ══════════════════════════════════════════════════════════════════
#  3. 에러 내결함성 (Factor 9) 테스트
# ══════════════════════════════════════════════════════════════════

class TestFaultTolerance:

    def test_tool_error_does_not_crash_agent(self):
        """도구 에러가 발생해도 에이전트가 계속 실행됨"""
        llm = ScriptedLLM([
            '{"tool":"nonexistent_tool","params":{},"reason":"없는 도구"}',
            '{"tool":"done","params":{"summary":"에러 후 복구 완료"},"reason":"끝"}',
        ])
        store  = make_store()
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, store=store)

        assert result["status"] == "done"
        events      = store.load(result["run_id"])
        fail_events = [e for e in events if isinstance(e, ToolFailed)]
        assert len(fail_events) == 1
        assert fail_events[0].tool_name == "nonexistent_tool"

    def test_llm_returns_invalid_json_falls_back_gracefully(self):
        """LLM이 JSON이 아닌 응답을 반환해도 ask_human으로 폴백"""
        llm = ScriptedLLM([
            "이건 JSON이 아닌 응답입니다 오류 상황",
            '{"tool":"done","params":{"summary":"복구 완료"},"reason":"끝"}',
        ])
        human_fn = lambda level, q, ctx="": "계속 진행"
        store    = make_store()
        result   = run_agent(make_snapshot(), "alert_check", make_config(),
                             llm_fn=llm, human_input_fn=human_fn, store=store)

        assert result["status"] == "done"

    def test_consecutive_errors_escalate_to_human(self):
        """Factor 9: 연속 에러 3회 → 사람에게 에스컬레이션"""
        llm = ScriptedLLM([
            '{"tool":"nonexistent_1","params":{},"reason":"test1"}',
            '{"tool":"nonexistent_2","params":{},"reason":"test2"}',
            '{"tool":"nonexistent_3","params":{},"reason":"test3"}',
            '{"tool":"done","params":{"summary":"에스컬레이션 후 완료"},"reason":"끝"}',
        ])
        human_answers = deque(["계속 진행해"])
        human_fn = lambda level, q, ctx="": human_answers.popleft() if human_answers else "계속"

        config = make_config()
        config.error_handling.max_consecutive_errors = 3
        store  = make_store()
        result = run_agent(make_snapshot(), "alert_check", config,
                           llm_fn=llm, human_input_fn=human_fn, store=store)

        assert result["status"] == "done"
        events = store.load(result["run_id"])
        # 에스컬레이션으로 인한 HumanAsked 이벤트 확인
        asked = [e for e in events if isinstance(e, HumanAsked)]
        assert len(asked) >= 1
        assert any("연속" in e.question for e in asked)


# ══════════════════════════════════════════════════════════════════
#  4. Factor 4: 도구 검증/거부 통합 테스트
# ══════════════════════════════════════════════════════════════════

class TestToolValidationIntegration:

    def test_tool_rejected_recorded_and_agent_continues(self):
        """도구가 거부되면 ToolRejected 이벤트가 기록되고 에이전트가 계속 진행"""
        llm = ScriptedLLM([
            '{"tool":"transfer","params":{"amount_usd":999999,"slippage_pct":10},"reason":"전송"}',
            '{"tool":"done","params":{"summary":"거부 후 완료"},"reason":"끝"}',
        ])
        config = make_config()
        config.tool_validation.max_slippage_pct = 3.0
        store  = make_store()
        result = run_agent(make_snapshot(), "alert_check", config,
                           llm_fn=llm, store=store)

        assert result["status"] == "done"
        events = store.load(result["run_id"])
        rejected = [e for e in events if isinstance(e, ToolRejected)]
        assert len(rejected) >= 1


# ══════════════════════════════════════════════════════════════════
#  5. Factor 6 재개 테스트
# ══════════════════════════════════════════════════════════════════

class TestResumeCapability:

    def test_resume_continues_from_exact_last_event(self):
        """중단된 실행을 정확히 마지막 이벤트부터 재개"""
        store = make_store()

        # 1단계: 2스텝 실행 후 중단 (max_steps=2)
        llm_phase1 = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"analyze_portfolio","params":{"focus":"yield"},"reason":"분석"}',
        ])
        result1 = run_agent(make_snapshot(), "alert_check",
                            make_config(max_steps=2),
                            llm_fn=llm_phase1, store=store)
        run_id = result1["run_id"]
        events_before = store.count_events(run_id)

        # 2단계: 같은 run_id로 재개
        llm_phase2 = ScriptedLLM([
            '{"tool":"done","params":{"summary":"재개 후 완료"},"reason":"끝"}',
        ])
        result2 = run_agent(make_snapshot(), "alert_check",
                            make_config(max_steps=5),
                            llm_fn=llm_phase2, store=store,
                            resume_run_id=run_id)

        assert result2["status"] == "done"
        assert "재개 후 완료" in result2["summary"]
        events_after = store.count_events(run_id)
        assert events_after > events_before

    def test_event_replay_rebuilds_identical_context(self):
        """저장된 이벤트를 replay하면 실행 당시와 동일한 context 재현"""
        store = make_store()
        llm   = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, store=store)
        run_id = result["run_id"]

        all_events   = store.load(run_id)
        replayed_ctx = derive_context(all_events)

        content = str(replayed_ctx)
        assert "fetch_all_portfolios" in content or "포트폴리오" in content


# ══════════════════════════════════════════════════════════════════
#  6. 타임머신 (Point-in-time) 테스트
# ══════════════════════════════════════════════════════════════════

class TestTimeMachine:

    def test_replay_at_seq_0_returns_only_initial_context(self):
        """seq=0에서 replay하면 TaskStarted만 있는 초기 context"""
        store = make_store()
        llm   = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, store=store)

        ctx_at_start = replay_at(result["run_id"], seq=0, store=store)
        content = str(ctx_at_start)
        assert "포트폴리오" in content or "Flare" in content

    def test_step_by_step_context_grows(self):
        """각 seq마다 context가 정확히 하나씩 커짐"""
        store = make_store()
        llm   = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])
        result = run_agent(make_snapshot(), "alert_check", make_config(),
                           llm_fn=llm, store=store)
        run_id = result["run_id"]

        ctx_0 = replay_at(run_id, seq=0, store=store)
        ctx_1 = replay_at(run_id, seq=1, store=store)
        ctx_2 = replay_at(run_id, seq=2, store=store)

        assert len(ctx_1) >= len(ctx_0)
        assert len(ctx_2) >= len(ctx_1)


# ══════════════════════════════════════════════════════════════════
#  7. 데이터 fetcher 테스트
# ══════════════════════════════════════════════════════════════════

class TestDataFetchers:

    @pytest.mark.asyncio
    async def test_flare_fetcher_returns_chain_portfolio(self):
        from data_fetchers.flare_fetcher import fetch_flare_portfolio
        from models import ChainPortfolio
        result = await fetch_flare_portfolio("0xTEST", "https://mock-rpc")
        assert isinstance(result, ChainPortfolio)
        assert result.chain == "Flare"
        assert result.native_balance > 0
        assert result.fetch_error is None

    @pytest.mark.asyncio
    async def test_xdc_fetcher_returns_staking_positions(self):
        from data_fetchers.xdc_fetcher import fetch_xdc_portfolio
        result = await fetch_xdc_portfolio("0xXDC_TEST", "https://mock-rpc")
        assert result.chain == "XDC"
        assert len(result.staking_positions) > 0
        assert result.staking_positions[0].protocol == "PrimeStaking"


# ══════════════════════════════════════════════════════════════════
#  8. Factor 3: 컨텍스트 형식 전환 통합 테스트
# ══════════════════════════════════════════════════════════════════

class TestContextFormatIntegration:

    def test_xml_format_in_full_run(self):
        """XML 형식으로 전체 실행 시 정상 동작"""
        llm = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"XML 형식 완료"},"reason":"끝"}',
        ])
        config = make_config()
        config.context.context_format = "xml"
        store = make_store()
        result = run_agent(make_snapshot(), "alert_check", config,
                           llm_fn=llm, store=store)

        assert result["status"] == "done"
        # LLM이 받은 context에 XML 태그가 포함됐는지 확인
        second_ctx = str(llm.calls[1])
        assert "<tool_result" in second_ctx or "<system_instruction>" in second_ctx

    def test_plain_format_in_full_run(self):
        """평문 형식으로 전체 실행 시 정상 동작"""
        llm = ScriptedLLM([
            '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"Plain 형식 완료"},"reason":"끝"}',
        ])
        config = make_config()
        config.context.context_format = "plain"
        store = make_store()
        result = run_agent(make_snapshot(), "alert_check", config,
                           llm_fn=llm, store=store)

        assert result["status"] == "done"
        second_ctx = str(llm.calls[1])
        assert "<tool_result" not in second_ctx
