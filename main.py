# main.py — 진입점 (CLI + 스케줄러)
#
# Factor 10: Multi-Agent 아키텍처의 진입점.
#   --mode controller (기본): Controller가 Sub-Agent 오케스트레이션
#   --mode single:            기존 단일 에이전트 모드 (하위 호환)
#
# Factor 11: Outer Loop Agent — Cron/이벤트로 트리거, 사람에게 연락

import asyncio
import argparse
from datetime import datetime

from data_fetchers.flare_fetcher import fetch_flare_portfolio
from data_fetchers.xdc_fetcher   import fetch_xdc_portfolio
from data_fetchers.xrpl_fetcher  import fetch_xrpl_portfolio
from models      import PortfolioSnapshot, ChainPortfolio
from config      import CONFIG
from loop        import run_agent, make_anthropic_llm, _auto_approve_input
from agents.controller import run_controller
from event_store import EventStore
from logger      import setup_logger
from address_validator import validate_wallets, InvalidWalletAddress

log = setup_logger("main")


async def collect_snapshot(wallets: dict) -> PortfolioSnapshot:
    """루프 시작 전 모든 데이터 병렬 수집. wallets 는 validate_wallets 로 검증된 dict 여야 한다."""
    fetchers = {
        "FLR": lambda addr: fetch_flare_portfolio(addr, CONFIG.chains.flare_rpc),
        "XDC": lambda addr: fetch_xdc_portfolio(addr, CONFIG.chains.xdc_rpc),
        "XRP": lambda addr: fetch_xrpl_portfolio(addr, CONFIG.chains.xrpl_ws),
    }
    tasks = []
    keys  = []
    for key, addr in wallets.items():
        fetcher = fetchers.get(key)
        if not fetcher:
            continue
        tasks.append(fetcher(addr))
        keys.append(key)

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
    parser = argparse.ArgumentParser(description="DeFi Portfolio Agent v5 — 12-Factor Multi-Agent")
    parser.add_argument("--task", default="daily_report",
                        help="작업 (daily_report, alert_check, rebalance, full_analysis, ...)")
    # 지갑 주소: CLI > env (WALLET_FLARE/WALLET_XDC/WALLET_XRPL) > 생략.
    # 명시적으로 주어지지 않은 체인은 스냅샷 수집에서 제외된다.
    parser.add_argument("--wallet-flare", default=None, help="Flare 지갑 (기본: env WALLET_FLARE)")
    parser.add_argument("--wallet-xdc",   default=None, help="XDC 지갑 (기본: env WALLET_XDC)")
    parser.add_argument("--wallet-xrpl",  default=None, help="XRPL 지갑 (기본: env WALLET_XRPL)")
    parser.add_argument("--auto",         action="store_true", help="자동 승인 모드")
    parser.add_argument("--mode",         choices=["controller", "single"], default="controller",
                        help="Factor 10: controller(Multi-Agent) | single(단일 에이전트)")
    parser.add_argument("--resume",       metavar="RUN_ID",    help="중단된 실행 재개")
    parser.add_argument("--list-runs",    action="store_true", help="재개 가능한 실행 목록")
    parser.add_argument("--context-format", choices=["xml", "plain", "single"], default="xml",
                        help="Factor 3: 컨텍스트 형식 (single=원문 패턴)")
    args = parser.parse_args()

    store = EventStore()

    if args.list_runs:
        runs = store.list_resumable()
        if not runs:
            print("재개 가능한 실행 없음")
        for r in runs:
            print(f"  {r['run_id']} | {r['task']} | {r['status']} | {r['updated_at']}")
        return

    config = CONFIG
    config.context.context_format = args.context_format

    # 지갑 주소 병합: CLI > env. 어느 쪽도 주지 않은 체인은 제외.
    cli_overrides = {
        "FLR": args.wallet_flare,
        "XDC": args.wallet_xdc,
        "XRP": args.wallet_xrpl,
    }
    env_wallets = config.wallets.as_dict()
    merged = {
        k: (cli_overrides.get(k) or env_wallets.get(k))
        for k in ("FLR", "XDC", "XRP")
    }
    merged = {k: v for k, v in merged.items() if v}
    if not merged:
        log.error("지갑 주소가 설정되지 않음. .env 의 WALLET_* 또는 --wallet-* CLI 인자를 사용하세요.")
        raise SystemExit(2)
    try:
        wallets = validate_wallets(merged)
    except InvalidWalletAddress as e:
        log.error(f"지갑 주소 검증 실패: {e}")
        raise SystemExit(2)

    log.info(f"온체인 데이터 수집 시작 ({', '.join(wallets.keys())})")
    snapshot = await collect_snapshot(wallets)
    log.info(f"수집 완료: ${snapshot.total_value_usd:,.2f}")

    llm_fn = make_anthropic_llm(config.model)
    human_fn = _auto_approve_input if args.auto else None

    if args.mode == "controller":
        # ── Factor 10: Multi-Agent 모드 (기본) ────────────────────
        log.info("Controller Agent 모드 (Multi-Agent)")
        result = run_controller(
            snapshot       = snapshot,
            task           = args.task,
            config         = config,
            llm_fn         = llm_fn,
            human_input_fn = human_fn,
            store          = store,
        )
    else:
        # ── 단일 에이전트 모드 (하위 호환) ────────────────────────
        log.info("단일 에이전트 모드")
        result = run_agent(
            snapshot       = snapshot,
            task           = args.task,
            config         = config,
            llm_fn         = llm_fn,
            human_input_fn = human_fn,
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
