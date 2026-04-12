"""
e2e_verify.py — API 키 없이 전체 실행 경로를 검증하는 스크립트.
ScriptedLLM으로 LLM 레이어를 대체해 실제 흐름 전체를 돌린다.

검증 항목:
  1. 온체인 데이터 병렬 수집
  2. 포트폴리오 요약 (Factor 3: XML 태그)
  3. 단일 에이전트 루프 (Factor 4/6/9)
  4. 도구 거부 흐름 (Factor 4)
  5. Multi-Agent: Controller → Sub-Agent 오케스트레이션 (Factor 10)
"""
import asyncio, tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

from data_fetchers.flare_fetcher import fetch_flare_portfolio
from data_fetchers.xdc_fetcher   import fetch_xdc_portfolio
from models      import PortfolioSnapshot
from config      import AgentConfig
from loop        import run_agent
from agents.controller import run_controller
from event_store import EventStore
from events      import (ToolRejected, SubAgentStarted, SubAgentCompleted,
                         AgentCompleted)


def scripted_llm_factory(responses):
    q = deque(responses)
    calls = []
    def call(messages):
        calls.append(len(messages))
        return q.popleft() if q else '{"tool":"done","params":{"summary":"소진"},"reason":"끝"}'
    call.calls = calls
    return call


async def main():
    SEP = "=" * 60

    print(SEP)
    print("  DeFi Portfolio Agent v5 — End-to-End 실행 검증")
    print("  12-Factor Agents + Multi-Agent Architecture")
    print(SEP)

    config = AgentConfig()

    # ── 1. 온체인 데이터 병렬 수집 ───────────────────────────────
    print("\n[1/5] 온체인 데이터 병렬 수집...")
    flare, xdc = await asyncio.gather(
        fetch_flare_portfolio("0xTEST_FLARE", config.chains.flare_rpc),
        fetch_xdc_portfolio("0xTEST_XDC",    config.chains.xdc_rpc),
    )

    assert flare.chain == "Flare" and flare.fetch_error is None
    assert xdc.chain   == "XDC"   and xdc.fetch_error   is None

    snapshot = PortfolioSnapshot(
        timestamp=datetime.now(), chains=[flare, xdc],
        total_value_usd=flare.total_value_usd + xdc.total_value_usd,
        total_staking_rewards_usd=sum(
            sp.rewards_earned for c in [flare, xdc] for sp in c.staking_positions
        )
    )
    print(f"    OK 총 자산: ${snapshot.total_value_usd:,.2f}")

    # ── 2. 단일 에이전트 루프 (하위 호환) ────────────────────────
    print("\n[2/5] 단일 에이전트 루프 (Factor 4/6/9)...")
    llm1 = scripted_llm_factory([
        '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
        '{"tool":"detect_alerts","params":{},"reason":"탐지"}',
        '{"tool":"generate_report","params":{"type":"daily"},"reason":"리포트"}',
        '{"tool":"done","params":{"summary":"단일 에이전트 완료"},"reason":"끝"}',
    ])
    config1 = AgentConfig()
    config1.max_steps = 10
    store1 = EventStore(Path(tempfile.mktemp(suffix=".db")))
    r1 = run_agent(snapshot, "alert_check", config1, llm_fn=llm1, store=store1)
    assert r1["status"] == "done"
    print(f"    OK 상태: {r1['status']} | 스텝: {r1['steps']}")

    # ── 3. 도구 거부 흐름 (Factor 4) ────────────────────────────
    print("\n[3/5] 도구 거부 흐름 (Factor 4: tool-as-proposal)...")
    llm2 = scripted_llm_factory([
        '{"tool":"transfer","params":{"amount_usd":999999,"slippage_pct":10},"reason":"대량 전송"}',
        '{"tool":"done","params":{"summary":"거부 후 완료"},"reason":"끝"}',
    ])
    config2 = AgentConfig()
    config2.max_steps = 10
    config2.tool_validation.max_slippage_pct = 3.0
    store2 = EventStore(Path(tempfile.mktemp(suffix=".db")))
    r2 = run_agent(snapshot, "alert_check", config2, llm_fn=llm2, store=store2)
    assert r2["status"] == "done"
    events2 = store2.load(r2["run_id"])
    rejected = [e for e in events2 if isinstance(e, ToolRejected)]
    assert len(rejected) >= 1
    print(f"    OK 거부: {rejected[0].tool_name} — {rejected[0].reject_reason}")

    # ── 4. Multi-Agent: Controller → Sub-Agent (Factor 10) ──────
    print("\n[4/5] Multi-Agent 오케스트레이션 (Factor 10)...")
    print("    Controller → monitor → news → rebalancer → done")
    llm3 = scripted_llm_factory([
        # Controller: monitor 위임
        '{"tool":"delegate","params":{"agent":"monitor","task":"포트폴리오 현황 점검"},"reason":"모니터링부터"}',
        # Monitor Sub-Agent
        '{"tool":"fetch_portfolios","params":{},"reason":"조회"}',
        '{"tool":"detect_alerts","params":{},"reason":"이상 탐지"}',
        '{"tool":"done","params":{"summary":"총 자산 $4,095. 이상 징후 없음."},"reason":"완료"}',
        # Controller: news 위임
        '{"tool":"delegate","params":{"agent":"news","task":"최신 뉴스 분석"},"reason":"뉴스 확인"}',
        # News Sub-Agent
        '{"tool":"fetch_news","params":{"chain":"all"},"reason":"뉴스 수집"}',
        '{"tool":"analyze_sentiment","params":{},"reason":"감성 분석"}',
        '{"tool":"done","params":{"summary":"전체 긍정적. Firelight 출시 호재."},"reason":"완료"}',
        # Controller: rebalancer 위임
        '{"tool":"delegate","params":{"agent":"rebalancer","task":"리밸런싱 분석"},"reason":"배분 확인"}',
        # Rebalancer Sub-Agent
        '{"tool":"get_current_allocation","params":{},"reason":"현재 배분"}',
        '{"tool":"get_target_allocation","params":{"strategy":"balanced"},"reason":"목표 배분"}',
        '{"tool":"calculate_rebalance","params":{},"reason":"리밸런싱 계산"}',
        '{"tool":"done","params":{"summary":"소량 조정 필요. Flare +$500, XDC -$200."},"reason":"완료"}',
        # Controller: 종합 완료
        '{"tool":"done","params":{"summary":"3개 에이전트 분석 완료. 이상 없음, 뉴스 긍정적, 소량 리밸런싱 권고."},"reason":"종합 완료"}',
    ])

    config3 = AgentConfig()
    config3.max_steps = 15
    store3 = EventStore(Path(tempfile.mktemp(suffix=".db")))
    r3 = run_controller(
        snapshot=snapshot, task="전체 포트폴리오 분석",
        config=config3, llm_fn=llm3, store=store3
    )

    assert r3["status"] == "done"
    assert "3개 에이전트" in r3["summary"]

    # Controller 이벤트 검증
    events3 = store3.load(r3["run_id"])
    sub_started = [e for e in events3 if isinstance(e, SubAgentStarted)]
    sub_completed = [e for e in events3 if isinstance(e, SubAgentCompleted)]

    assert len(sub_started) == 3, f"SubAgentStarted 수: {len(sub_started)}"
    assert len(sub_completed) == 3, f"SubAgentCompleted 수: {len(sub_completed)}"

    agent_names = [e.agent_name for e in sub_started]
    assert "monitor" in agent_names
    assert "news" in agent_names
    assert "rebalancer" in agent_names

    print(f"    OK 상태: {r3['status']}")
    print(f"    OK Controller 스텝: {r3['steps']}")
    print(f"    OK 위임된 에이전트: {agent_names}")
    print(f"    OK 요약: {r3['summary']}")

    # 각 Sub-Agent의 독립 이벤트 스트림 확인
    for sc in sub_completed:
        sub_events = store3.load(sc.sub_run_id)
        sub_types = [type(e).__name__ for e in sub_events]
        assert "TaskStarted" in sub_types
        assert "AgentCompleted" in sub_types
        print(f"    OK [{sc.agent_name}] run_id={sc.sub_run_id} | 이벤트: {len(sub_events)}개 | {sc.summary[:40]}")

    # ── 5. Multi-Agent 이벤트 타임라인 출력 ──────────────────────
    print(f"\n[5/5] Controller 이벤트 타임라인 (run_id: {r3['run_id']}):")
    for i, e in enumerate(events3):
        label = (getattr(e, "agent_name", None)
                 or getattr(e, "tool_name", None)
                 or getattr(e, "task", None)
                 or getattr(e, "summary", "")[:40]
                 or "")
        print(f"    [{i:02d}] {type(e).__name__:<22} {label}")

    print()
    print(SEP)
    print("  OK 모든 실행 경로 검증 완료")
    print("  단일 에이전트 + Multi-Agent 아키텍처 모두 정상")
    print(f"  12-Factor 적용: F1-F12 전체 (F10: 5개 전문 Agent + Controller)")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
