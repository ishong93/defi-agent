# DeFi Portfolio Monitoring Agent v3
## 12-Factor Agents 원칙 기반 구현

### 프로젝트 구조

```
defi-agent-v3/
├── CLAUDE.md                     # Claude Code 가이드라인
├── README.md                     # 이 파일
├── requirements.txt              # 의존성
├── e2e_verify.py                 # End-to-End 실행 검증 스크립트
│
├── events.py                     # Factor 12: 불변 이벤트 타입 정의
├── reducer.py                    # Factor 12: 순수 리듀서 (events → context)
├── event_store.py                # Factor 6:  Append-only 이벤트 저장소
├── loop.py                       # Factor 6,7,8: 에이전트 메인 루프
│
├── prompts.py                    # Factor 2:  프롬프트 소유
├── context.py                    # Factor 3:  컨텍스트 소유
├── tools.py                      # Factor 4,5: 툴 레지스트리 + 상태 통합
├── models.py                     # 공유 데이터 모델 (dataclass)
├── config.py                     # 설정 및 알림 임계값
├── logger.py                     # 구조화 로깅 (run_id 기반)
├── retry.py                      # 지수 백오프 재시도 데코레이터
│
├── data_fetchers/
│   ├── flare_fetcher.py          # Flare Network (FXRP, stXRP, SparkDEX)
│   └── xdc_fetcher.py            # XDC Network (PrimeStaking)
│
└── tests/
    ├── test_architecture.py      # Factor 12 순수 리듀서 + Factor 6 이벤트 저장소
    └── test_integration.py       # 전체 에이전트 흐름 통합 테스트
```

### 적용된 12-Factor 원칙

| Factor | 파일 | 내용 |
|--------|------|------|
| F2 Own your prompts | `prompts.py` | 시스템 프롬프트 직접 소유 |
| F3 Own your context | `context.py`, `reducer.py` | 컨텍스트 직접 제어 |
| F4 Tools = structured outputs | `tools.py` | 툴은 구조화된 출력 |
| F5 Unify state | `models.py` | 실행/비즈니스 상태 통합 |
| F6 Launch/Pause/Resume | `event_store.py`, `loop.py` | 이벤트 기반 재개 |
| F7 Contact humans | `loop.py` | ask_human 툴 호출 |
| F8 Own your control flow | `loop.py` | 루프 직접 제어 |
| F9 Compact errors | `reducer.py`, `context.py` | 에러 압축 |
| F12 Stateless reducer | `reducer.py`, `events.py` | 이벤트 소싱 패턴 |
| F13 Pre-fetch context | `e2e_verify.py` | 루프 전 병렬 수집 |

### 테스트 결과

```
단위/통합 테스트:  26 / 26 통과 (경고 0개)
End-to-End 실행:   5 /  5 경로 통과 (에러 0개)
```

### 빠른 시작

```bash
# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# ANTHROPIC_API_KEY, FLARE_RPC_URL 등 설정

# 테스트 실행
python -m pytest tests/ -v

# End-to-End 검증 (API 키 불필요)
python e2e_verify.py

# 실제 실행
python -c "
import asyncio
from e2e_verify import main
asyncio.run(main())
"
```

### 실제 API 연결

```python
from loop import run_agent, make_anthropic_llm
from config import CONFIG

result = run_agent(
    snapshot  = snapshot,          # PortfolioSnapshot
    task      = 'alert_check',     # daily_report | alert_check | rebalance
    config    = CONFIG,
    llm_fn    = make_anthropic_llm(CONFIG.model),  # 실제 Anthropic API
)
```

### 재개(Resume) 사용법

```bash
# 목록 확인
python main.py --list-runs

# 재개
python main.py --resume run_20260410_091523_a3f2b1
```

### 지원 네트워크

- **Flare Network**: FLR, FXRP, stXRP (Firelight Finance ERC-4626), SparkDEX LP
- **XDC Network**: XDC, PrimeStaking 위임 스테이킹
- **XRP Ledger**: XRP, earnXRP, AMM (xrpl-py)
