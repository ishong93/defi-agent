# tests/test_multi_agent.py — Factor 10: Multi-Agent 아키텍처 테스트
#
# 검증 항목:
#   1. Sub-Agent가 독립적으로 실행되고 결과를 반환
#   2. Controller가 Sub-Agent에게 위임하고 결과를 수집
#   3. 각 Sub-Agent가 작은 max_steps로 제한
#   4. Agent→Agent 통신이 이벤트로 기록
#   5. Sub-Agent 실패가 Controller로 전파

import pytest
import tempfile
from pathlib import Path
from datetime import datetime
from collections import deque

from agents.base import run_sub_agent, SubAgentResult
from agents.registry import (get_all_agent_specs, build_monitor_tools,
                              build_news_tools, build_rebalancer_tools,
                              build_tax_tools, MONITOR_PROMPT, NEWS_PROMPT)
from agents.controller import run_controller
from events import (SubAgentStarted, SubAgentCompleted, LLMResponded,
                    ToolSucceeded, AgentCompleted, AgentFailed)
from event_store import EventStore
from reducer import derive_context
from models import PortfolioSnapshot, ChainPortfolio, StakingPosition, DeFiPosition
from config import AgentConfig


# ── 공통 픽스처 ───────────────────────────────────────────────────

def make_snapshot(total_usd=12500.0) -> PortfolioSnapshot:
    chain = ChainPortfolio(
        chain="Flare", wallet_address="0xTEST_WALLET",
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
        chain="XDC", wallet_address="0xXDC_WALLET",
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


def make_config() -> AgentConfig:
    return AgentConfig()


class ScriptedLLM:
    def __init__(self, responses: list[str]):
        self.queue = deque(responses)
        self.calls = []

    def __call__(self, messages: list) -> str:
        self.calls.append(messages)
        if self.queue:
            return self.queue.popleft()
        return '{"tool":"done","params":{"summary":"스크립트 소진"},"reason":"끝"}'


# ══════════════════════════════════════════════════════════════════
#  1. Sub-Agent 독립 실행 테스트
# ══════════════════════════════════════════════════════════════════

class TestSubAgentExecution:

    def test_monitor_agent_runs_independently(self):
        """Monitor Sub-Agent가 독립적으로 실행되고 결과를 반환"""
        snapshot = make_snapshot()
        llm = ScriptedLLM([
            '{"tool":"fetch_portfolios","params":{},"reason":"포트폴리오 조회"}',
            '{"tool":"detect_alerts","params":{},"reason":"이상 탐지"}',
            '{"tool":"done","params":{"summary":"이상 징후 없음. 총 자산 $12,500."},"reason":"완료"}',
        ])

        result = run_sub_agent(
            agent_name="monitor",
            system_prompt=MONITOR_PROMPT,
            tools=build_monitor_tools(snapshot),
            task="포트폴리오 현황 점검",
            snapshot=snapshot,
            config=make_config(),
            llm_fn=llm,
            max_steps=6,
            store=make_store(),
        )

        assert isinstance(result, SubAgentResult)
        assert result.agent_name == "monitor"
        assert result.status == "done"
        assert result.steps == 3
        assert "이상 징후 없음" in result.summary

    def test_news_agent_runs_independently(self):
        """News Sub-Agent가 독립적으로 실행"""
        llm = ScriptedLLM([
            '{"tool":"fetch_news","params":{"chain":"Flare"},"reason":"뉴스 수집"}',
            '{"tool":"analyze_sentiment","params":{},"reason":"감성 분석"}',
            '{"tool":"done","params":{"summary":"전체적으로 긍정적. Firelight 출시 호재."},"reason":"완료"}',
        ])

        result = run_sub_agent(
            agent_name="news",
            system_prompt=NEWS_PROMPT,
            tools=build_news_tools(),
            task="최신 뉴스 분석",
            snapshot=make_snapshot(),
            config=make_config(),
            llm_fn=llm,
            max_steps=5,
            store=make_store(),
        )

        assert result.status == "done"
        assert result.steps == 3

    def test_sub_agent_max_steps_enforced(self):
        """Sub-Agent의 max_steps가 강제됨 (Factor 10: 컨텍스트 제한)"""
        llm = ScriptedLLM([
            '{"tool":"fetch_portfolios","params":{},"reason":"1"}',
            '{"tool":"detect_alerts","params":{},"reason":"2"}',
            '{"tool":"fetch_price_history","params":{"asset":"FLR"},"reason":"3"}',
            # max_steps=3이면 여기서 중단됨 — done 호출 못함
        ])

        result = run_sub_agent(
            agent_name="monitor",
            system_prompt=MONITOR_PROMPT,
            tools=build_monitor_tools(make_snapshot()),
            task="점검",
            snapshot=make_snapshot(),
            config=make_config(),
            llm_fn=llm,
            max_steps=3,  # 매우 작은 제한
            store=make_store(),
        )

        assert result.status == "max_steps_exceeded"
        assert result.steps == 3

    def test_sub_agent_tool_error_handled(self):
        """Sub-Agent 도구 에러가 정상 처리됨"""
        llm = ScriptedLLM([
            '{"tool":"nonexistent_tool","params":{},"reason":"없는 도구"}',
            '{"tool":"done","params":{"summary":"에러 후 복구"},"reason":"끝"}',
        ])

        result = run_sub_agent(
            agent_name="monitor",
            system_prompt=MONITOR_PROMPT,
            tools=build_monitor_tools(make_snapshot()),
            task="점검",
            snapshot=make_snapshot(),
            config=make_config(),
            llm_fn=llm,
            max_steps=5,
            store=make_store(),
        )

        assert result.status == "done"

    def test_sub_agent_events_persisted(self):
        """Sub-Agent의 이벤트가 독립 run_id로 저장됨"""
        store = make_store()
        llm = ScriptedLLM([
            '{"tool":"fetch_portfolios","params":{},"reason":"조회"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
        ])

        result = run_sub_agent(
            agent_name="monitor",
            system_prompt=MONITOR_PROMPT,
            tools=build_monitor_tools(make_snapshot()),
            task="점검",
            snapshot=make_snapshot(),
            config=make_config(),
            llm_fn=llm,
            store=store,
        )

        events = store.load(result.run_id)
        types = [type(e).__name__ for e in events]
        assert "TaskStarted" in types
        assert "AgentCompleted" in types


# ══════════════════════════════════════════════════════════════════
#  2. Controller Agent 오케스트레이션 테스트
# ══════════════════════════════════════════════════════════════════

class TestControllerOrchestration:

    def test_controller_delegates_to_sub_agents(self):
        """Controller가 Sub-Agent에게 위임하고 결과를 수집"""
        # Controller LLM: delegate → monitor, delegate → news, done
        # Monitor Sub-Agent LLM: fetch → detect → done
        # News Sub-Agent LLM: fetch_news → done
        llm_responses = deque([
            # Controller: monitor 위임
            '{"tool":"delegate","params":{"agent":"monitor","task":"포트폴리오 점검"},"reason":"모니터링"}',
            # Monitor Sub-Agent: 3 calls
            '{"tool":"fetch_portfolios","params":{},"reason":"조회"}',
            '{"tool":"detect_alerts","params":{},"reason":"탐지"}',
            '{"tool":"done","params":{"summary":"이상 없음"},"reason":"완료"}',
            # Controller: news 위임
            '{"tool":"delegate","params":{"agent":"news","task":"뉴스 분석"},"reason":"뉴스"}',
            # News Sub-Agent: 2 calls
            '{"tool":"fetch_news","params":{"chain":"all"},"reason":"수집"}',
            '{"tool":"done","params":{"summary":"긍정적 뉴스"},"reason":"완료"}',
            # Controller: 완료
            '{"tool":"done","params":{"summary":"모니터링 이상 없음 + 뉴스 긍정적"},"reason":"종합 완료"}',
        ])
        llm = ScriptedLLM(list(llm_responses))

        store = make_store()
        result = run_controller(
            snapshot=make_snapshot(),
            task="일간 포트폴리오 분석",
            config=make_config(),
            llm_fn=llm,
            store=store,
        )

        assert result["status"] == "done"
        assert "모니터링" in result["summary"]

        # Controller 이벤트에 SubAgentStarted/Completed가 기록됨
        events = store.load(result["run_id"])
        types = [type(e).__name__ for e in events]
        assert "SubAgentStarted" in types
        assert "SubAgentCompleted" in types

    def test_controller_handles_unknown_agent(self):
        """Controller가 존재하지 않는 에이전트 요청을 처리"""
        llm = ScriptedLLM([
            '{"tool":"delegate","params":{"agent":"nonexistent","task":"???"},"reason":"테스트"}',
            '{"tool":"done","params":{"summary":"에러 처리 후 완료"},"reason":"끝"}',
        ])

        result = run_controller(
            snapshot=make_snapshot(),
            task="테스트",
            config=make_config(),
            llm_fn=llm,
            store=make_store(),
        )

        assert result["status"] == "done"

    def test_controller_sub_agent_results_in_context(self):
        """Sub-Agent 결과가 Controller의 다음 LLM 호출 context에 포함됨"""
        llm = ScriptedLLM([
            # Controller: monitor 위임
            '{"tool":"delegate","params":{"agent":"monitor","task":"점검"},"reason":"위임"}',
            # Monitor: 바로 done
            '{"tool":"done","params":{"summary":"모니터 결과: 정상"},"reason":"완료"}',
            # Controller: done — 이 시점에서 monitor 결과가 context에 있어야 함
            '{"tool":"done","params":{"summary":"최종 완료"},"reason":"끝"}',
        ])

        store = make_store()
        result = run_controller(
            snapshot=make_snapshot(),
            task="점검",
            config=make_config(),
            llm_fn=llm,
            store=store,
        )

        assert result["status"] == "done"
        # Controller의 마지막 LLM 호출(3번째)에서 monitor 결과가 보이는지 확인
        last_call = llm.calls[-1]  # 마지막 LLM 호출의 messages
        content = str(last_call)
        assert "모니터 결과" in content or "monitor" in content.lower()

    def test_sub_agent_events_have_separate_run_ids(self):
        """Sub-Agent와 Controller는 별도의 run_id를 가짐"""
        llm = ScriptedLLM([
            '{"tool":"delegate","params":{"agent":"monitor","task":"점검"},"reason":"위임"}',
            '{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
            '{"tool":"done","params":{"summary":"최종"},"reason":"끝"}',
        ])

        store = make_store()
        result = run_controller(
            snapshot=make_snapshot(),
            task="점검",
            config=make_config(),
            llm_fn=llm,
            store=store,
        )

        # Controller의 이벤트에서 SubAgentCompleted의 sub_run_id 확인
        events = store.load(result["run_id"])
        sub_completed = [e for e in events if isinstance(e, SubAgentCompleted)]
        assert len(sub_completed) >= 1
        assert sub_completed[0].sub_run_id != result["run_id"]  # 별도 run_id


# ══════════════════════════════════════════════════════════════════
#  3. Agent 레지스트리 테스트
# ══════════════════════════════════════════════════════════════════

class TestAgentRegistry:

    def test_all_agents_registered(self):
        """5개 전문 에이전트가 모두 등록됨"""
        specs = get_all_agent_specs(make_snapshot())
        assert set(specs.keys()) == {"monitor", "news", "trader", "rebalancer", "tax"}

    def test_each_agent_has_focused_tools(self):
        """각 에이전트는 자기 역할에 맞는 도구만 가짐"""
        specs = get_all_agent_specs(make_snapshot())

        # Monitor는 fetch_portfolios, detect_alerts 가짐
        assert "fetch_portfolios" in specs["monitor"].tools
        assert "detect_alerts" in specs["monitor"].tools
        # Monitor에는 calculate_tax 없음
        assert "calculate_tax" not in specs["monitor"].tools

        # Tax에는 detect_alerts 없음
        assert "calculate_tax" in specs["tax"].tools
        assert "detect_alerts" not in specs["tax"].tools

    def test_each_agent_has_small_max_steps(self):
        """Factor 10: 각 에이전트의 max_steps가 작게 제한됨"""
        specs = get_all_agent_specs(make_snapshot())
        for name, spec in specs.items():
            assert spec.max_steps <= 8, f"{name} max_steps가 너무 큼: {spec.max_steps}"

    def test_each_agent_has_unique_prompt(self):
        """Factor 2: 각 에이전트가 고유한 시스템 프롬프트를 가짐"""
        specs = get_all_agent_specs(make_snapshot())
        prompts = [spec.system_prompt for spec in specs.values()]
        # 모두 다른 프롬프트인지 확인
        assert len(set(prompts)) == len(prompts), "중복된 프롬프트 발견"


# ══════════════════════════════════════════════════════════════════
#  4. SubAgent 이벤트 저장/로드 테스트
# ══════════════════════════════════════════════════════════════════

class TestSubAgentEvents:

    def test_sub_agent_events_in_context(self):
        """SubAgentCompleted 이벤트가 context에 올바르게 포함됨"""
        from events import TaskStarted, SubAgentStarted, SubAgentCompleted

        events = [
            TaskStarted(task="test", portfolio_summary="data"),
            LLMResponded(raw_output='{"tool":"delegate"}', tool_name="delegate"),
            SubAgentStarted(agent_name="monitor", task="점검"),
            SubAgentCompleted(agent_name="monitor", status="done",
                              summary="이상 없음", sub_run_id="sub_123"),
        ]
        ctx = derive_context(events)
        full = " ".join(m["content"] for m in ctx)
        assert "monitor" in full.lower()
        assert "이상 없음" in full

    def test_sub_agent_events_stored_and_loaded(self):
        """SubAgent 이벤트가 EventStore에서 정상 저장/로드"""
        from events import SubAgentStarted, SubAgentCompleted

        store = make_store()
        store.start_run("test_run", "test")
        store.append("test_run", SubAgentStarted(agent_name="news", task="뉴스"))
        store.append("test_run", SubAgentCompleted(
            agent_name="news", status="done",
            summary="긍정적", sub_run_id="sub_456"
        ))

        events = store.load("test_run")
        assert len(events) == 2
        assert isinstance(events[0], SubAgentStarted)
        assert events[0].agent_name == "news"
        assert isinstance(events[1], SubAgentCompleted)
        assert events[1].sub_run_id == "sub_456"
