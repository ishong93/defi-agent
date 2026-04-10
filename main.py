# main.py — 진입점 (CLI + 스케줄러)
import asyncio
import argparse
from datetime import datetime
from pathlib import Path

from data_fetchers.flare_fetcher import fetch_flare_portfolio
from data_fetchers.xdc_fetcher   import fetch_xdc_portfolio
from models      import PortfolioSnapshot, ChainPortfolio
from config      import CONFIG
from loop        import run_agent, make_anthropic_llm, _auto_approve_input
from event_store import EventStore
from logger      import setup_logger

log = setup_logger("main")


async def collect_snapshot(wallets: dict) -> PortfolioSnapshot:
    """Factor 13: 루프 시작 전 모든 데이터 병렬 수집"""
    tasks = []
    keys  = []
    if "FLR" in wallets:
        tasks.append(fetch_flare_portfolio(wallets["FLR"], CONFIG.chains.flare_rpc))
        keys.append("FLR")
    if "XDC" in wallets:
        tasks.append(fetch_xdc_portfolio(wallets["XDC"], CONFIG.chains.xdc_rpc))
        keys.append("XDC")

    results = await asyncio.gather(*tasks, return_exceptions=True)

    chains = []
    for key, result in zip(keys, results):
        if isinstance(result, Exception):
            log.error(f"{key} 수집 실패: {result}")
            chains.append(ChainPortfolio(
                chain=key, wallet_address=wallets[key],
                native_balance=0, native_price_usd=0, total_value_usd=0,
                fetch_error=str(result)
            ))
        else:
            chains.append(result)

    total_usd     = sum(c.total_value_usd for c in chains)
    total_rewards = sum(sp.rewards_earned for c in chains for sp in c.staking_positions)

    return PortfolioSnapshot(
        timestamp=datetime.now(), chains=chains,
        total_value_usd=total_usd, total_staking_rewards_usd=total_rewards,
    )


async def main():
    parser = argparse.ArgumentParser(description="DeFi Portfolio Agent v3")
    parser.add_argument("--task", choices=["daily_report","alert_check","rebalance","weekly_report"],
                        default="alert_check")
    parser.add_argument("--wallet-flare", default="0xYOUR_FLARE_WALLET")
    parser.add_argument("--wallet-xdc",   default="0xYOUR_XDC_WALLET")
    parser.add_argument("--auto",         action="store_true", help="자동 승인 모드")
    parser.add_argument("--resume",       metavar="RUN_ID",    help="중단된 실행 재개")
    parser.add_argument("--list-runs",    action="store_true", help="재개 가능한 실행 목록")
    args = parser.parse_args()

    store = EventStore()

    if args.list_runs:
        runs = store.list_resumable()
        if not runs:
            print("재개 가능한 실행 없음")
        for r in runs:
            print(f"  {r['run_id']} | {r['task']} | {r['status']} | {r['updated_at']}")
        return

    log.info("온체인 데이터 수집 시작")
    snapshot = await collect_snapshot({
        "FLR": args.wallet_flare,
        "XDC": args.wallet_xdc,
    })
    log.info(f"수집 완료: ${snapshot.total_value_usd:,.2f}")

    result = run_agent(
        snapshot       = snapshot,
        task           = args.task,
        config         = CONFIG,
        llm_fn         = make_anthropic_llm(CONFIG.model),
        human_input_fn = _auto_approve_input if args.auto else None,
        resume_run_id  = args.resume,
        store          = store,
    )

    print(f"\n{'='*50}")
    print(f"상태:  {result['status']}")
    print(f"RunID: {result['run_id']}")
    if result.get("steps"):
        print(f"스텝:  {result['steps']}")
    if result.get("summary"):
        print(f"요약:  {result['summary']}")
    if result["status"] == "paused":
        print(f"\n재개: python main.py --resume {result['run_id']}")


if __name__ == "__main__":
    asyncio.run(main())
