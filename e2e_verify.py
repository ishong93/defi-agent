"""
e2e_verify.py — API 키 없이 전체 실행 경로를 검증하는 스크립트.
ScriptedLLM으로 LLM 레이어를 대체해 실제 흐름 전체를 돌린다.
"""
import asyncio, tempfile
from collections import deque
from datetime import datetime
from pathlib import Path

from data_fetchers.flare_fetcher import fetch_flare_portfolio
from data_fetchers.xdc_fetcher   import fetch_xdc_portfolio
from models      import PortfolioSnapshot
from config      import CONFIG
from loop        import run_agent
from event_store import EventStore


def scripted_llm_factory(responses):
    """사전 정의된 응답을 순서대로 반환하는 목 LLM"""
    q = deque(responses)
    calls = []
    def call(messages):
        calls.append(len(messages))
        return q.popleft() if q else '{"tool":"done","params":{"summary":"소진"},"reason":"끝"}'
    call.calls = calls
    return call


async def main():
    SEP = "=" * 55

    print(SEP)
    print("  DeFi Portfolio Agent v3 — End-to-End 실행 검증")
    print(SEP)

    # ── 1. 온체인 데이터 병렬 수집 ───────────────────────────────
    print("\n[1/4] 온체인 데이터 병렬 수집 (Factor 13)...")
    flare, xdc = await asyncio.gather(
        fetch_flare_portfolio("0xTEST_FLARE", CONFIG.chains.flare_rpc),
        fetch_xdc_portfolio("0xTEST_XDC",    CONFIG.chains.xdc_rpc),
    )

    assert flare.chain == "Flare" and flare.fetch_error is None, "Flare 수집 실패"
    assert xdc.chain   == "XDC"   and xdc.fetch_error   is None, "XDC 수집 실패"

    snapshot = PortfolioSnapshot(
        timestamp=datetime.now(), chains=[flare, xdc],
        total_value_usd=flare.total_value_usd + xdc.total_value_usd,
        total_staking_rewards_usd=sum(
            sp.rewards_earned for c in [flare, xdc] for sp in c.staking_positions
        )
    )
    print(f"    ✅ Flare: ${flare.total_value_usd:,.2f}")
    print(f"    ✅ XDC:   ${xdc.total_value_usd:,.2f}")
    print(f"    ✅ 총 자산: ${snapshot.total_value_usd:,.2f}")

    # ── 2. 포트폴리오 요약 확인 ───────────────────────────────────
    print("\n[2/4] 포트폴리오 요약 출력:")
    summary = snapshot.to_context_summary()
    for line in summary.split("\n")[:9]:
        if line.strip():
            print(f"    {line}")
    assert "Flare" in summary and "XDC" in summary, "요약에 체인 정보 누락"
    assert "Firelight" in summary, "스테이킹 정보 누락"
    print("    ✅ 요약 내용 검증 완료")

    # ── 3. 에이전트 루프 실행 ─────────────────────────────────────
    print("\n[3/4] 에이전트 루프 실행...")
    llm = scripted_llm_factory([
        '{"tool":"fetch_all_portfolios","params":{},"reason":"전체 포트폴리오 조회"}',
        '{"tool":"detect_alerts","params":{},"reason":"이상 징후 탐지"}',
        '{"tool":"analyze_portfolio","params":{"focus":"yield"},"reason":"수익률 분석"}',
        '{"tool":"fetch_defi_yields","params":{"chain":"Flare"},"reason":"Flare 수익률 조회"}',
        '{"tool":"generate_report","params":{"type":"daily"},"reason":"일간 리포트 생성"}',
        '{"tool":"send_telegram_alert","params":{"message":"일간 리포트 완료","level":"info"},"reason":"알림 발송"}',
        '{"tool":"done","params":{"summary":"이상 없음. 일간 리포트 생성 및 텔레그램 알림 발송 완료."},"reason":"완료"}',
    ])

    cfg           = CONFIG
    cfg.max_steps = 15
    store         = EventStore(Path(tempfile.mktemp(suffix=".db")))

    result = run_agent(snapshot, "alert_check", cfg, llm_fn=llm, store=store)

    assert result["status"] == "done",          f"상태 오류: {result['status']}"
    assert result["steps"]  == 7,               f"스텝 수 오류: {result['steps']}"
    assert "완료" in result["summary"],          "완료 요약 누락"

    print(f"    ✅ 상태:      {result['status']}")
    print(f"    ✅ 실행 스텝: {result['steps']}")
    print(f"    ✅ 총 이벤트: {result['total_events']}")
    print(f"    ✅ 요약:      {result['summary']}")
    print(f"    ✅ LLM 호출:  {len(llm.calls)}회  |  호출별 context 크기: {llm.calls}")

    # ── 4. 이벤트 저장소 검증 ─────────────────────────────────────
    print("\n[4/4] 이벤트 저장소 검증...")
    events  = store.load(result["run_id"])
    etypes  = [type(e).__name__ for e in events]

    assert etypes[0]  == "TaskStarted",   "첫 이벤트가 TaskStarted가 아님"
    assert etypes[-1] == "AgentCompleted","마지막 이벤트가 AgentCompleted가 아님"
    assert etypes.count("LLMResponded")   == 7, f"LLMResponded 수 오류: {etypes.count('LLMResponded')}"
    assert etypes.count("ToolSucceeded")  == 6, f"ToolSucceeded 수 오류: {etypes.count('ToolSucceeded')}"

    for i, e in enumerate(events):
        label = (getattr(e, "tool_name", None)
                 or getattr(e, "task", None)
                 or getattr(e, "summary", "")[:40]
                 or "")
        print(f"    [{i:02d}] {type(e).__name__:<22} {label}")

    # ── 5. 재개(Resume) 경로 검증 ────────────────────────────────
    print("\n[5/5] Factor 6 재개 경로 검증...")
    run_id = result["run_id"]

    # 2스텝 실행 후 중단
    llm2   = scripted_llm_factory([
        '{"tool":"fetch_all_portfolios","params":{},"reason":"조회"}',
        '{"tool":"analyze_portfolio","params":{"focus":"risk"},"reason":"리스크 분석"}',
    ])
    cfg2           = CONFIG
    cfg2.max_steps = 2
    store2         = EventStore(Path(tempfile.mktemp(suffix=".db")))
    r_partial      = run_agent(snapshot, "alert_check", cfg2, llm_fn=llm2, store=store2)
    run_id2        = r_partial["run_id"]
    events_before  = store2.count_events(run_id2)
    print(f"    ✅ 1차 실행: {r_partial['status']} | 저장된 이벤트: {events_before}개")

    # 재개
    llm3  = scripted_llm_factory([
        '{"tool":"generate_report","params":{"type":"daily"},"reason":"리포트"}',
        '{"tool":"done","params":{"summary":"재개 후 완료"},"reason":"끝"}',
    ])
    cfg3           = CONFIG
    cfg3.max_steps = 10
    r_resumed      = run_agent(snapshot, "alert_check", cfg3, llm_fn=llm3,
                               store=store2, resume_run_id=run_id2)
    events_after   = store2.count_events(run_id2)

    assert r_resumed["status"] == "done",       "재개 후 완료 실패"
    assert events_after > events_before,        "재개 후 이벤트 누적 안됨"
    print(f"    ✅ 재개 성공: {r_resumed['status']} | 총 이벤트: {events_after}개 (재개 전 {events_before}개)")
    print(f"    ✅ 요약: {r_resumed['summary']}")

    print()
    print(SEP)
    print("  ✅ 모든 실행 경로 검증 완료 — 에러 없음")
    print(SEP)


if __name__ == "__main__":
    asyncio.run(main())
