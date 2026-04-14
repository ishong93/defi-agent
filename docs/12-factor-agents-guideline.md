# 12-Factor Agents 코딩 가이드라인

> 이 문서는 [humanlayer/12-factor-agents](https://github.com/humanlayer/12-factor-agents) 원칙을 실제 프로젝트에 적용하기 위한 종합 가이드라인입니다.
> 레퍼런스 구현체: DeFi Portfolio Agent (본 저장소)

---

## Part 1: 개요 및 아키텍처

### 1.1 12-Factor Agents란?

12-Factor Agents는 프로덕션 수준의 LLM 에이전트를 구축하기 위한 12가지 설계 원칙이다. 핵심 철학은 다음 한 문장으로 요약된다:

> **"LLM은 텍스트를 입력받아 텍스트를 출력하는 함수일 뿐이다. 제어권은 코드가 가진다."**

대부분의 에이전트 프레임워크(LangChain, CrewAI 등)는 편의를 위해 프롬프트, 컨텍스트, 제어 흐름을 추상화한다. 이 추상화가 프로토타입에서는 빠르지만, 프로덕션에서는 디버깅 불가, 테스트 불가, 확장 불가의 벽에 부딪힌다. 12-Factor는 이 추상화를 걷어내고, 개발자가 모든 것을 직접 소유하라고 말한다.

### 1.2 12 Factor 한눈에 보기

| # | Factor | 한줄 요약 |
|---|--------|-----------|
| 1 | Natural Language to Structured Output | LLM 출력은 반드시 구조화된 JSON이어야 한다 |
| 2 | Own Your Prompts | 프롬프트를 프레임워크에 맡기지 말고 코드로 직접 관리하라 |
| 3 | Own Your Context Window | 컨텍스트 윈도우는 개발자가 정밀하게 제어하라 |
| 4 | Tools are Just Structured Outputs | 도구 호출은 LLM의 "제안"이지 "명령"이 아니다 |
| 5 | Unify Execution and Business State | 실행 상태와 비즈니스 상태를 하나의 이벤트에 통합하라 |
| 6 | Launch, Pause, Resume | 에이전트는 언제든 중단하고 재개할 수 있어야 한다 |
| 7 | Contact Humans with Tool Calls | 사람에게 연락하는 것도 도구 호출이다 |
| 8 | Own Your Control Flow | 제어 흐름(while 루프)을 직접 소유하라 |
| 9 | Compact Errors into Context | 에러를 압축하여 컨텍스트에 반영하라 |
| 10 | Small, Focused Agents | 큰 에이전트를 작은 전문 에이전트로 분해하라 |
| 11 | Trigger from Anywhere | CLI, 웹훅, 크론 등 어디서든 트리거 가능하게 하라 |
| 12 | Make Your Agent a Stateless Reducer | 에이전트를 순수 함수(stateless reducer)로 만들라 |
| 13 | Pre-fetch Context (Appendix) | 에이전트 시작 전에 필요한 데이터를 미리 수집하라 |

### 1.3 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────┐
│                        진입점 (F11)                          │
│              CLI / Webhook / Cron / Slack                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       v
┌──────────────────────────────────────────────────────────────┐
│                 Pre-fetch (F13)                               │
│          에이전트 시작 전 외부 데이터 수집                      │
│          (API, DB, 온체인 등 → Snapshot)                      │
└──────────────────────┬───────────────────────────────────────┘
                       │
                       v
┌──────────────────────────────────────────────────────────────┐
│              Controller Agent (F10)                           │
│     ┌────────────────────────────────────────┐                │
│     │          Agent Loop (F8)               │                │
│     │  ┌─────────────────────────────────┐   │                │
│     │  │ 1. derive_context(events) (F12) │   │                │
│     │  │ 2. LLM 호출 → JSON (F1)        │   │                │
│     │  │ 3. 검증 (F4)                    │   │                │
│     │  │ 4. 실행 or 위임                 │   │                │
│     │  └─────────────────────────────────┘   │                │
│     └────────────────────────────────────────┘                │
│           │         │         │         │                     │
│           v         v         v         v                     │
│     ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐              │
│     │Sub-Agent│ │Sub-Agent│ │Sub-Agent│ │Sub-Agent│             │
│     │ (F10)  │ │ (F10)  │ │ (F10)  │ │ (F10)  │              │
│     │3-10 stp│ │3-10 stp│ │3-10 stp│ │3-10 stp│              │
│     └────────┘ └────────┘ └────────┘ └────────┘              │
└──────────────────────────────────────────────────────────────┘
        │                                        │
        v                                        v
┌───────────────┐                    ┌──────────────────┐
│ Event Store   │                    │  Human (F7)      │
│ (F5, F6, F12) │                    │  ask_human 도구   │
│ append-only   │                    │  = 도구 호출과    │
│ SQLite        │                    │    동일한 패턴    │
└───────────────┘                    └──────────────────┘
```

### 1.4 핵심 파일 구조

12-Factor 에이전트 프로젝트의 권장 디렉토리 구조:

```
my-agent/
├── main.py              # F11: 진입점 (CLI + 스케줄러)
├── config.py            # 설정값 중앙 관리 (dataclass)
├── prompts.py           # F2: 프롬프트 문자열 관리
├── events.py            # F5/F12: 불변 이벤트 타입 정의
├── reducer.py           # F12: derive_context() 순수 함수
├── tools.py             # F4: 파싱, 검증, 실행 분리
├── loop.py              # F8: 메인 에이전트 루프
├── event_store.py       # F6: append-only 이벤트 저장소
├── models.py            # 도메인 데이터 모델
├── logger.py            # 로깅 인프라
├── agents/              # F10: 멀티 에이전트
│   ├── controller.py    # Controller (오케스트레이터)
│   ├── base.py          # Sub-Agent 실행 엔진
│   └── registry.py      # Sub-Agent 명세 + 도구 세트
├── data_fetchers/       # F13: 사전 데이터 수집기
│   ├── api_fetcher.py
│   └── db_fetcher.py
├── tests/
│   ├── test_architecture.py  # 단위 테스트 (reducer, tools, events)
│   ├── test_integration.py   # 통합 테스트 (ScriptedLLM)
│   └── test_multi_agent.py   # 멀티 에이전트 테스트
└── state/
    └── events.db        # F6: SQLite 이벤트 저장소
```

### 1.5 핵심 데이터 흐름

```
이벤트 스트림 (append-only):
  TaskStarted → LLMResponded → ToolSucceeded → LLMResponded → ... → AgentCompleted

컨텍스트 생성 (매 스텝):
  events[] ──→ derive_context(events) ──→ messages[] ──→ LLM API ──→ JSON

상태 복원 (재개 시):
  EventStore.load(run_id) ──→ events[] ──→ derive_context() ──→ 이어서 실행
```

---

## Part 2: Factor별 코딩 가이드라인

> 각 Factor는 **원칙 요약 → 왜 중요한가 → 구현 패턴 → 체크리스트 → 안티패턴** 순서로 설명한다.
> 코드 예시는 도메인 독립적으로 일반화했으며, 어떤 LLM 에이전트 프로젝트에든 적용 가능하다.

---

### Factor 1: Natural Language to Structured Output

#### 원칙 요약

LLM의 출력은 반드시 구조화된 형식(JSON)이어야 한다. 자연어 응답을 파싱하려고 정규식을 쓰는 순간, 시스템은 깨지기 시작한다.

#### 왜 중요한가

- 자연어 파싱은 **비결정적**이다 — "네, 해보겠습니다"와 "좋습니다, 진행합니다"를 구분하는 정규식은 없다
- JSON 파싱은 **결정적**이다 — 성공 아니면 실패, 중간은 없다
- 파싱 실패 시 프레임워크가 "대신 판단"하면 Factor 7 위반이다 (LLM만이 의사결정자)

#### 구현 패턴

**1) LLM 출력 형식을 프롬프트에서 강제한다:**

```python
# prompts.py — 도구 정의를 JSON 예시로 프롬프트에 명시

SYSTEM_PROMPT = """
당신은 [도메인] 전문 에이전트입니다.

## 사용 가능한 도구 (JSON 형식으로만 반환)
{"tool": "search_data", "params": {"query": "..."}, "reason": "검색 이유"}
{"tool": "analyze", "params": {"focus": "..."}, "reason": "분석 이유"}
{"tool": "ask_human", "params": {"level": "info|warning|critical", "question": "..."}, "reason": "확인 필요"}
{"tool": "done", "params": {"summary": "..."}, "reason": "완료"}

## 규칙
- JSON 외 다른 텍스트 절대 금지
- 한 번에 하나의 도구만 선택
""".strip()
```

**2) 파서는 실패 시 예외를 던진다 (절대 대신 판단하지 않는다):**

```python
# tools.py

def parse_tool_call(llm_output: str) -> dict:
    """LLM 출력에서 JSON 도구 호출을 파싱한다.
    실패 시 ValueError — 프레임워크가 대신 판단하지 않는다."""
    try:
        text = llm_output.strip()
        if "```" in text:
            text = text.split("```")[1].lstrip("json").strip()
        return json.loads(text)
    except Exception as e:
        raise ValueError(f"JSON 파싱 실패: {e} | 출력: {llm_output[:150]}")
```

**3) 파싱 실패는 에러 이벤트로 기록하고 LLM에게 재시도를 요청한다:**

```python
# loop.py — 메인 루프 내부

try:
    tool_call = parse_tool_call(raw_output)
except ValueError as e:
    # 파싱 실패를 이벤트로 기록
    err = ToolFailed(
        tool_name="__parse_error__",
        error_type="JSONParseError",
        error_msg=f"JSON 형식 오류. JSON만 출력하세요. 원본: {raw_output[:100]}"
    )
    store.append(run_id, err)
    events.append(err)
    continue  # 다음 루프에서 LLM이 에러를 보고 수정
```

#### 체크리스트

- [ ] 시스템 프롬프트에 JSON 출력 형식이 명시되어 있는가?
- [ ] `parse_tool_call()`이 실패 시 예외를 던지는가? (fallback 없이)
- [ ] 파싱 실패가 이벤트로 기록되어 LLM이 다음 스텝에서 수정할 수 있는가?

#### 안티패턴

```python
# BAD: 파싱 실패 시 프레임워크가 대신 판단
def parse_tool_call(output):
    try:
        return json.loads(output)
    except:
        return {"tool": "ask_human", "params": {"question": output}}  # 위반!
```

이것이 왜 나쁜가: LLM이 "포트폴리오가 위험합니다"라고 자연어를 출력했을 때, 프레임워크가 이를 `ask_human`으로 변환하면 **프레임워크가 LLM 대신 의사결정**을 한 것이다. LLM은 `done`을 의도했을 수도, `detect_alerts`를 의도했을 수도 있다.

---

### Factor 2: Own Your Prompts

#### 원칙 요약

프롬프트는 에이전트의 "영혼"이다. 프레임워크에 위임하지 말고, 코드에서 직접 관리하고, A/B 테스트하고, 버전 관리하라.

#### 왜 중요한가

- 프레임워크가 프롬프트를 숨기면 **디버깅이 불가능**하다 — LLM이 왜 그런 결정을 했는지 알 수 없다
- 프롬프트 한 글자가 행동을 바꾼다 — 이것을 통제할 수 없으면 시스템을 통제할 수 없다
- "flexibility to try EVERYTHING" — Role Hacking, XML 래핑, 예시 교체 등 자유롭게 실험해야 한다

#### 구현 패턴

**1) 프롬프트를 전용 파일에서 관리한다:**

```python
# prompts.py — 모든 프롬프트 문자열의 단일 소스

SYSTEM_PROMPT = """...""".strip()

# 용도별 프롬프트 분리
REPORT_PROMPT = """
다음 데이터를 바탕으로 {report_type} 리포트를 작성하세요.
## 구성
1. 전체 현황 요약
2. 상세 분석
...
## 데이터
{data}
""".strip()

# XML 컨텍스트 템플릿 (Factor 3과 연결)
XML_CONTEXT_TEMPLATES = {
    "system":      "<system_instruction>\n{content}\n</system_instruction>",
    "tool_result": "<tool_result name=\"{name}\">\n{content}\n</tool_result>",
    "tool_error":  "<tool_error name=\"{name}\">\n{error_type}: {message}\n</tool_error>",
    ...
}
```

**2) 에이전트별로 프롬프트를 분리한다 (Factor 10 멀티 에이전트 시):**

```python
# agents/registry.py — Sub-Agent별 전용 프롬프트

MONITOR_PROMPT = """
당신은 모니터링 전문 에이전트입니다.
역할: 현황 조회, 이상 징후 탐지.

## 사전 수집 데이터 (Factor 13)
데이터는 이미 컨텍스트에 포함되어 있습니다.
바로 분석을 시작하세요.

## 도구 (JSON 형식으로만 반환)
{"tool": "detect_alerts", "params": {}, "reason": "이상 징후 탐지"}
{"tool": "done", "params": {"summary": "..."}, "reason": "완료"}
""".strip()

TRADER_PROMPT = """...""".strip()  # 트레이딩 전용
```

**3) Role Hacking — system 역할 대신 user/assistant 쌍으로 주입:**

```python
# reducer.py — 시스템 프롬프트를 user 메시지로 주입

return [
    {"role": "user",      "content": f"<system_instruction>\n{SYSTEM_PROMPT}\n</system_instruction>"},
    {"role": "assistant", "content": "네, JSON만 반환하겠습니다."},
] + messages
```

#### 체크리스트

- [ ] 모든 프롬프트가 `prompts.py` (또는 전용 파일)에서 관리되는가?
- [ ] 프롬프트가 git으로 버전 관리되는가?
- [ ] 에이전트별로 프롬프트가 분리되어 있는가?
- [ ] 프롬프트 형식을 쉽게 교체할 수 있는가? (A/B 테스트)

#### 안티패턴

```python
# BAD: 프레임워크에 프롬프트를 위임
agent = SomeFramework.create_agent(
    role="portfolio analyst",
    goal="analyze DeFi portfolios",
    tools=[...],
)
# 프레임워크가 내부적으로 프롬프트를 생성 — 내용을 알 수 없고 제어할 수 없다
```

---

### Factor 3: Own Your Context Window

#### 원칙 요약

컨텍스트 윈도우는 LLM이 보는 "세상의 전부"다. 개발자가 이 창에 무엇이 들어가는지 정밀하게 제어해야 한다. Karpathy는 이것을 "context engineering"이라 부른다.

#### 왜 중요한가

- 컨텍스트가 길어지면 LLM의 성능이 열화된다 ("lost in the middle" 현상)
- 불필요한 정보가 들어가면 LLM이 혼동한다
- 같은 이벤트 목록이라도 **형식**에 따라 LLM의 행동이 달라진다

#### 구현 패턴

**1) 다양한 컨텍스트 형식을 지원한다 (A/B 테스트):**

```python
# reducer.py

def derive_context(events: list, context_format: str = "xml") -> list[dict]:
    """Factor 12 순수 함수: 이벤트 → messages[]
    
    context_format:
      "xml"    — XML 태그로 구조화 (기본값, 권장)
      "plain"  — 기존 평문 형식
      "single" — 모든 이벤트를 단일 user 메시지에 결합 (원문 패턴)
    """
    if context_format == "single":
        return _derive_single_message(events)
    
    fmt = _xml_formatter if context_format == "xml" else _plain_formatter
    messages = []
    resolved_tools = _find_resolved_errors(events)  # F9: 해결된 에러 제거
    
    for i, event in enumerate(events):
        match event:
            case ToolSucceeded(tool_name=name, result=result):
                messages.append({
                    "role": "user",
                    "content": fmt("tool_result", name=name, content=result)
                })
            case ToolFailed(tool_name=name, error_type=etype, error_msg=emsg):
                if i in resolved_tools:
                    continue  # F9: 이후에 같은 도구가 성공했으면 건너뛴다
                messages.append({
                    "role": "user",
                    "content": fmt("tool_error", name=name, 
                                   error_type=etype, message=emsg[:200])
                })
            case LLMResponded(raw_output=output):
                messages.append({"role": "assistant", "content": output})
            # ... 기타 이벤트 처리
    
    return [system_prompt_msg, assistant_ack_msg] + messages
```

**2) XML 형식 — 구조적 구분자로 LLM 혼동 방지:**

```python
# "xml" 형식 예시 (LLM이 보는 실제 컨텍스트)

<system_instruction>
당신은 분석 전문 에이전트입니다...
</system_instruction>

<tool_result name="fetch_data">
총 자산: $12,500.00
</tool_result>

<tool_error name="send_alert">
APIError: rate limit exceeded
</tool_error>
```

**3) Single-message 형식 — 원문의 `thread_to_prompt()` 패턴:**

```python
def _derive_single_message(events: list) -> list[dict]:
    """모든 이벤트를 XML 태그로 변환하여 단일 user 메시지에 결합."""
    parts = [f"<system_instruction>\n{SYSTEM_PROMPT}\n</system_instruction>"]
    
    for event in events:
        match event:
            case TaskStarted(task=task, portfolio_summary=summary):
                parts.append(f"<task_started>\n<data>\n{summary}\n</data>\n</task_started>")
            case LLMResponded(raw_output=output):
                parts.append(f"<agent_action>\n{output}\n</agent_action>")
            case ToolSucceeded(tool_name=name, result=result):
                parts.append(f"<tool_result name=\"{name}\">\n{result}\n</tool_result>")
            # ...
    
    parts.append("\nWhat should the next step be?")
    return [{"role": "user", "content": "\n\n".join(parts)}]
```

**4) CLI에서 형식을 선택할 수 있게 한다:**

```python
# main.py
parser.add_argument("--context-format", choices=["xml", "plain", "single"], default="xml")
```

#### 체크리스트

- [ ] `derive_context()`가 순수 함수인가? (사이드 이펙트 없음)
- [ ] 최소 2가지 이상의 컨텍스트 형식을 지원하는가?
- [ ] 해결된 에러가 컨텍스트에서 제거되는가? (Factor 9)
- [ ] 컨텍스트 형식을 설정으로 전환 가능한가? (A/B 테스트)

#### 안티패턴

```python
# BAD: 모든 이벤트를 무조건 전부 넣기
messages = [{"role": "user", "content": str(event)} for event in events]
# → 에러, 성공, 에러, 성공이 반복되며 LLM이 혼동
```

---

### Factor 4: Tools are Just Structured Outputs

#### 원칙 요약

LLM이 선택한 도구 호출은 **"제안(proposal)"**이지 **"명령(command)"**이 아니다. 코드가 최종 결정권을 가진다. Selection → Validation → Execution 3단계로 분리하라.

#### 왜 중요한가

- LLM은 할루시네이션할 수 있다 — `transfer(amount=999999)`를 제안할 수 있다
- 코드가 검증 없이 실행하면 치명적 사고가 발생한다
- 검증 단계에서 사람 확인을 삽입할 수 있다 (Factor 7과 연결)

#### 구현 패턴

**1) 검증 결과 타입을 정의한다:**

```python
# tools.py

@dataclass
class ValidationResult:
    approved: bool
    reject_reason: str | None = None
    requires_human: bool = False
    human_question: str | None = None
```

**2) 도메인별 검증 규칙을 구현한다:**

```python
def validate_tool_call(tool_call: dict, config) -> ValidationResult:
    """LLM의 제안을 코드가 검토하는 단계."""
    tool = tool_call.get("tool", "")
    params = tool_call.get("params", {})
    
    # done, ask_human은 항상 허용
    if tool in ("done", "ask_human"):
        return ValidationResult(approved=True)
    
    # 도메인별 검증 규칙 (예: 금액 한도)
    amount = params.get("amount_usd", 0)
    if amount > config.max_transfer_usd:
        return ValidationResult(
            approved=False,
            reject_reason=f"금액 한도 초과: ${amount:,.0f} > 최대 ${config.max_transfer_usd:,.0f}"
        )
    
    # 일정 금액 이상은 사람 확인 필수
    if amount > config.require_human_above_usd:
        return ValidationResult(
            approved=False,
            requires_human=True,
            human_question=f"${amount:,.0f} 규모 작업을 승인하시겠습니까?"
        )
    
    return ValidationResult(approved=True)
```

**3) 메인 루프에서 Selection → Validation → Execution을 분리한다:**

```python
# loop.py — 3단계 파이프라인

# Step 1: Selection — LLM이 도구를 선택 (제안)
raw_output = llm_fn(messages)
tool_call = parse_tool_call(raw_output)

# Step 2: Validation — 코드가 검증 (최종 결정)
validation = validate_tool_call(tool_call, config)
if not validation.approved:
    if validation.requires_human:
        # 사람에게 승인 요청 → Factor 7
        answer = human_input_fn("warning", validation.human_question)
        if answer not in ("yes", "승인"):
            events.append(ToolRejected(tool_name=..., reject_reason=...))
            continue
    else:
        events.append(ToolRejected(tool_name=..., reject_reason=validation.reject_reason))
        continue  # LLM이 거부 사유를 보고 수정된 제안을 한다

# Step 3: Execution — 검증 통과 시에만 실행
result = executor.dispatch(tool_call)
events.append(ToolSucceeded(tool_name=..., result=result))
```

**4) 거부된 도구 호출도 이벤트로 기록한다:**

```python
# events.py

@dataclass(frozen=True)
class ToolRejected(BaseEvent):
    """도구 호출이 검증에서 거부됨 — LLM이 수정된 제안을 할 수 있도록"""
    kind:            Literal["ToolRejected"] = "ToolRejected"
    tool_name:       str = ""
    reject_reason:   str = ""       # "왜" 거부됐는지
    original_params: str = ""       # 거부된 원래 파라미터 (JSON)
```

#### 체크리스트

- [ ] Selection(LLM 선택)과 Execution(실행)이 분리되어 있는가?
- [ ] 검증 단계(`validate_tool_call`)가 존재하는가?
- [ ] 거부된 도구 호출이 `ToolRejected` 이벤트로 기록되는가?
- [ ] LLM이 거부 사유를 보고 수정된 제안을 할 수 있는가?
- [ ] 고위험 작업에 사람 확인이 삽입되는가?

#### 안티패턴

```python
# BAD: LLM 출력을 검증 없이 바로 실행
result = tool_call.get("tool")
params = tool_call.get("params")
execute_tool(result, params)  # 위험! 검증 없음
```

---

### Factor 5: Unify Execution State and Business State

#### 원칙 요약

에이전트의 "실행 상태"(어디까지 진행했는가)와 "비즈니스 상태"(도메인 데이터)를 하나의 이벤트 스트림에 통합하라. 별도의 상태 저장소를 두지 마라.

#### 왜 중요한가

- 실행 상태와 비즈니스 상태를 분리하면 **동기화 문제**가 생긴다
- "3번째 스텝에서 무슨 데이터를 봤는가?"를 재현할 수 없다
- 이벤트 하나에 둘 다 포함하면 replay만으로 모든 것을 재구성할 수 있다

#### 구현 패턴

**1) 이벤트에 실행 상태와 비즈니스 데이터를 함께 담는다:**

```python
# events.py — 각 이벤트가 "무엇이 일어났는지"를 완전히 기록

@dataclass(frozen=True)
class TaskStarted(BaseEvent):
    """에이전트 실행 시작 — 실행 상태 + 비즈니스 데이터 통합"""
    kind:              Literal["TaskStarted"] = "TaskStarted"
    task:              str = ""                # 실행 상태: 어떤 작업인지
    portfolio_summary: str = ""               # 비즈니스 데이터: 시작 시점 스냅샷

@dataclass(frozen=True)
class ToolSucceeded(BaseEvent):
    """도구 실행 성공 — 실행 진행 + 비즈니스 결과"""
    kind:      Literal["ToolSucceeded"] = "ToolSucceeded"
    tool_name: str = ""                       # 실행 상태: 어떤 도구를 실행했는지
    result:    str = ""                        # 비즈니스 데이터: 실행 결과
```

**2) 도메인 모델에 `to_context_summary()` 메서드를 제공한다:**

```python
# models.py

@dataclass
class BusinessSnapshot:
    """도메인 데이터 스냅샷 — Factor 5: 이벤트에 포함될 비즈니스 상태"""
    timestamp: datetime
    data: list[DataItem]
    total_value: float
    
    def to_context_summary(self) -> str:
        """LLM 컨텍스트에 넣을 요약 문자열"""
        lines = [f"[스냅샷 — {self.timestamp.strftime('%Y-%m-%d %H:%M')}]"]
        lines.append(f"총 가치: ${self.total_value:,.2f}")
        for item in self.data:
            lines.append(f"  {item.name}: ${item.value:,.2f}")
        return "\n".join(lines)
```

#### 체크리스트

- [ ] `TaskStarted` 이벤트에 비즈니스 데이터(스냅샷)가 포함되는가?
- [ ] 이벤트만으로 모든 시점의 상태를 재구성할 수 있는가?
- [ ] 별도의 상태 DB 없이 이벤트 스트림이 유일한 상태 소스인가?

#### 안티패턴

```python
# BAD: 실행 상태와 비즈니스 상태를 분리 저장
class AgentState:
    current_step: int           # 실행 상태 → DB 테이블 A
    portfolio_data: dict        # 비즈니스 상태 → DB 테이블 B
    
# 문제: 3번째 스텝에서 어떤 portfolio_data를 봤는지 알 수 없다
```

---

### Factor 6: Launch, Pause, Resume

#### 원칙 요약

에이전트는 언제든 중단(pause)하고 재개(resume)할 수 있어야 한다. 이를 위해 이벤트를 append-only로 저장하고, 재개 시 이벤트를 replay하여 상태를 복원한다.

#### 왜 중요한가

- 사람의 응답을 기다리는 동안 에이전트 프로세스를 살려두는 것은 비효율적이다
- 서버 재시작, 배포, 장애 시에도 작업을 이어갈 수 있어야 한다
- "어제 3번째 스텝에서 무엇을 했는가?" — 타임머신(time travel) 디버깅이 가능해야 한다

#### 구현 패턴

**1) Append-only 이벤트 저장소를 구현한다:**

```python
# event_store.py

class EventStore:
    """한 번 쓴 이벤트는 절대 수정/삭제 불가 — Git 커밋과 같다."""
    
    def __init__(self, db_path="state/events.db"):
        self.conn = sqlite3.connect(str(db_path))
        self._init_schema()
    
    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id  TEXT NOT NULL,
                seq     INTEGER NOT NULL,
                kind    TEXT NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE(run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id  TEXT PRIMARY KEY,
                task    TEXT NOT NULL,
                status  TEXT NOT NULL DEFAULT 'running'
            );
        """)
    
    def append(self, run_id: str, event) -> int:
        """이벤트 추가 — 수정/삭제 메서드는 존재하지 않는다"""
        seq = self._next_seq(run_id)
        payload = json.dumps(dataclasses.asdict(event), ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO events VALUES (NULL,?,?,?,?)",
            (run_id, seq, event.kind, payload)
        )
        self.conn.commit()
        return seq
    
    def load(self, run_id: str) -> list:
        """전체 이벤트 로드 — replay 가능"""
        ...
    
    def load_until(self, run_id: str, seq: int) -> list:
        """특정 시점까지만 로드 — 타임머신"""
        ...
```

**2) 재개 시 스냅샷 신선도를 검사한다:**

```python
# loop.py

if resume_run_id:
    events = store.load(run_id)
    
    # 스냅샷이 오래됐으면 새 데이터로 교체
    stale_minutes = _check_snapshot_staleness(events, config)
    if stale_minutes > 0:
        refresh_event = SnapshotRefreshed(
            portfolio_summary=snapshot.to_context_summary(),
            stale_minutes=stale_minutes
        )
        store.append(run_id, refresh_event)
        events.append(refresh_event)
```

**3) 타임머신 디버깅을 지원한다:**

```python
def replay_at(run_id: str, seq: int, store=None, context_format="xml"):
    """특정 시점의 context를 재현 — 디버깅용 타임머신"""
    events = store.load_until(run_id, seq)
    return derive_context(events, context_format)
```

#### 체크리스트

- [ ] 이벤트 저장소에 수정/삭제 메서드가 **없는가**? (append-only)
- [ ] `resume_run_id`로 중단된 작업을 이어갈 수 있는가?
- [ ] 재개 시 스냅샷 신선도를 검사하는가?
- [ ] `load_until(seq)`로 특정 시점을 재현할 수 있는가?

#### 안티패턴

```python
# BAD: 결과(context)를 저장 — 중간 스텝 복원 불가
def save_state(run_id, context_messages):
    db.update("agents", {"context": json.dumps(context_messages)}, run_id=run_id)
    
# 문제: 3번째 스텝의 상태를 복원하려면? → 불가능
# 올바른 방법: 이벤트(원인)를 저장 → events[:3]으로 replay
```

---

### Factor 7: Contact Humans with Tool Calls

#### 원칙 요약

사람에게 연락하는 것도 도구 호출이다. LLM이 `ask_human`이라는 도구를 선택하면, 프레임워크는 사람에게 질문을 전달하고 응답을 받아 이벤트로 기록한다. **LLM은 항상 JSON만 출력한다** — 자연어로 질문하지 않는다.

#### 왜 중요한가

- LLM이 자연어로 질문하면, 그것이 "도구 실행 결과"인지 "사람에게 하는 질문"인지 구분이 불가능하다
- `ask_human`을 도구로 만들면, 다른 도구와 **동일한 파이프라인**(Selection → Validation → Execution)을 탄다
- 사람에게 연락하는 채널(CLI, Slack, 이메일)을 쉽게 교체할 수 있다
- 이 패턴은 Agent→Agent 통신에도 그대로 확장된다 (Factor 10)

#### 구현 패턴

**1) `ask_human`을 도구로 정의한다:**

```python
# 프롬프트에서 ask_human을 다른 도구와 동일하게 정의
{"tool": "ask_human", "params": {
    "level": "info|warning|critical",
    "question": "사용자에게 물을 질문",
    "context": "추가 컨텍스트"
}, "reason": "확인이 필요한 이유"}
```

**2) `HumanAsked`/`HumanResponded` 이벤트를 정의한다:**

```python
# events.py

@dataclass(frozen=True)
class HumanAsked(BaseEvent):
    """에이전트가 사람에게 확인을 요청함"""
    kind:            Literal["HumanAsked"] = "HumanAsked"
    level:           str = "info"       # info | warning | critical
    question:        str = ""
    context:         str = ""
    urgency:         str = "medium"     # low | medium | high (원문 패턴)
    response_format: str = "free_text"  # free_text | yes_no | multiple_choice

@dataclass(frozen=True)
class HumanResponded(BaseEvent):
    """사람이 응답함 — 감사 추적(audit trail)을 위한 approver 필드"""
    kind:     Literal["HumanResponded"] = "HumanResponded"
    answer:   str = ""
    approver: str | None = None         # 누가 승인했는지
```

**3) 메인 루프에서 `ask_human`을 처리한다:**

```python
# loop.py

if tool_name == "ask_human":
    p = tool_call.get("params", {})
    asked = HumanAsked(level=p.get("level", "info"),
                       question=p.get("question", ""),
                       context=p.get("context", ""))
    store.append(run_id, asked)
    events.append(asked)
    
    # human_input_fn은 주입 가능 — CLI, Slack, 이메일 등
    answer = human_input_fn(p["level"], p["question"], p.get("context", ""))
    
    resp = HumanResponded(answer=answer)
    store.append(run_id, resp)
    events.append(resp)
    continue  # LLM이 응답을 보고 다음 행동을 결정
```

**4) `human_input_fn`을 교체 가능하게 설계한다:**

```python
# CLI용
def _cli_human_input(level, question, context=""):
    print(f"[{level.upper()}] {question}")
    return input("답변: ").strip()

# 자동 승인 (테스트용)
def _auto_approve(level, question, context=""):
    return "자동 승인"

# Slack용 (예시)
def _slack_human_input(level, question, context=""):
    slack.post_message(channel, f"[{level}] {question}")
    return slack.wait_for_reply(channel, timeout=3600)
```

#### 체크리스트

- [ ] `ask_human`이 JSON 도구 형식으로 정의되어 있는가?
- [ ] `HumanAsked`에 `urgency`, `response_format` 필드가 있는가?
- [ ] `human_input_fn`이 주입 가능한가? (DI 패턴)
- [ ] 사람의 응답이 `HumanResponded` 이벤트로 기록되는가? (감사 추적)

#### 안티패턴

```python
# BAD: LLM이 자연어로 질문을 출력
# LLM 출력: "사용자님, 이 거래를 진행해도 될까요?"
# → 이것이 도구 실행 결과인지 질문인지 구분 불가
# → 프레임워크가 "대신 판단"해서 ask_human으로 변환 = Factor 1, 7 위반
```

---

### Factor 8: Own Your Control Flow

#### 원칙 요약

에이전트의 제어 흐름(while 루프)을 직접 소유하라. 프레임워크에 위임하지 마라. 세 가지 제어 흐름 패턴을 구현하라: (1) done → 동기 완료, (2) ask_human → 비동기 중단, (3) 도구 실행 → 다음 스텝.

#### 왜 중요한가

- 제어 흐름을 프레임워크에 위임하면, 에이전트가 "왜 멈췄는지" 알 수 없다
- 에러 처리, 중단, 재개, 스텝 제한 등을 세밀하게 제어할 수 없다
- 직접 소유하면 10줄짜리 while 루프로 충분하다 — 프레임워크보다 명확하다

#### 구현 패턴

**메인 에이전트 루프 — 전체 구조:**

```python
# loop.py — 핵심은 단순한 for 루프

def run_agent(snapshot, task, config, llm_fn, human_input_fn, store):
    run_id = new_run_id()
    events = [TaskStarted(task=task, portfolio_summary=snapshot.to_context_summary())]
    store.append(run_id, events[0])
    
    for step in range(config.max_steps):
        # 컨텍스트 압축 체크 (F10)
        if should_compact(events, config.max_context_messages):
            compaction = make_compaction_event(events)
            store.append(run_id, compaction)
            events.append(compaction)
        
        # Selection: LLM 호출 → JSON
        messages = derive_context(events, config.context_format)   # F12
        raw_output = llm_fn(messages)                               # F1
        tool_call = parse_tool_call(raw_output)
        
        # LLM 응답을 이벤트로 기록 (실행 전에!)
        llm_event = LLMResponded(raw_output=raw_output, tool_name=tool_call["tool"])
        store.append(run_id, llm_event)
        events.append(llm_event)
        
        # ── 패턴 1: done → 동기 완료 ──
        if tool_call["tool"] == "done":
            store.append(run_id, AgentCompleted(summary=tool_call["params"]["summary"]))
            return {"status": "done", "run_id": run_id}
        
        # ── 패턴 2: ask_human → 비동기 중단 ──
        if tool_call["tool"] == "ask_human":
            # ... HumanAsked/HumanResponded 이벤트 기록
            continue
        
        # ── 패턴 3: 도구 실행 → 다음 스텝 ──
        validation = validate_tool_call(tool_call, config)       # F4
        if not validation.approved:
            events.append(ToolRejected(...))
            continue
        
        try:
            result = executor.dispatch(tool_call)
            events.append(ToolSucceeded(tool_name=..., result=result))
        except Exception as e:
            events.append(ToolFailed(tool_name=..., error_msg=str(e)[:200]))  # F9
    
    # max_steps 도달
    return {"status": "max_steps_exceeded", "run_id": run_id}
```

#### 체크리스트

- [ ] 에이전트 루프가 직접 작성한 for/while 루프인가? (프레임워크 위임 아님)
- [ ] 세 가지 제어 흐름이 모두 구현되어 있는가? (done, ask_human, 도구 실행)
- [ ] `max_steps` 제한이 있는가?
- [ ] 에러 발생 시에도 루프가 계속되는가? (F9와 연결)

#### 안티패턴

```python
# BAD: 프레임워크에 제어 흐름을 위임
agent = Framework.Agent(tools=[...], max_iterations=10)
result = agent.run("daily report")  # 내부 루프를 알 수 없다
```

---

### Factor 9: Compact Errors into Context

#### 원칙 요약

에러를 200자로 압축하고, 같은 도구가 나중에 성공하면 이전 에러를 컨텍스트에서 제거하라. 연속 에러가 N회 이상이면 사람에게 에스컬레이션하라.

#### 왜 중요한가

- 에러 스택 트레이스 전체를 컨텍스트에 넣으면 LLM이 혼동한다
- 이미 해결된 에러가 컨텍스트에 남아있으면 LLM이 "아직 실패 중"이라고 오판한다
- 연속 에러 시 LLM이 무한 루프에 빠지지 않도록 사람이 개입해야 한다

#### 구현 패턴

**1) 에러 메시지를 압축한다:**

```python
# 에러 이벤트 생성 시 200자로 자른다
err = ToolFailed(
    tool_name=tool_name,
    error_type=type(e).__name__,
    error_msg=str(e)[:200]  # 200자 제한
)
```

**2) 해결된 에러를 컨텍스트에서 제거한다:**

```python
# reducer.py

def _find_resolved_errors(events: list) -> set[int]:
    """에러 후 같은 도구가 성공했으면, 그 에러의 인덱스를 반환."""
    resolved = set()
    succeeded_tools = set()
    for i in range(len(events) - 1, -1, -1):  # 역순 탐색
        if isinstance(events[i], ToolSucceeded):
            succeeded_tools.add(events[i].tool_name)
        elif isinstance(events[i], ToolFailed) and events[i].tool_name in succeeded_tools:
            resolved.add(i)  # 이 에러는 이후에 해결되었다
    return resolved
```

**3) 연속 에러 카운터로 에스컬레이션한다:**

```python
def count_consecutive_errors(events: list) -> int:
    """최근부터 역순으로 연속 에러 수를 센다."""
    count = 0
    for event in reversed(events):
        if isinstance(event, (ToolFailed, ToolRejected)):
            count += 1
        elif isinstance(event, (ToolSucceeded, TaskStarted, HumanResponded)):
            break  # 성공이나 사람 응답이 나오면 리셋
    return count

# 루프 내에서
consecutive = count_consecutive_errors(events)
if consecutive >= config.max_consecutive_errors:
    # 사람에게 에스컬레이션
    asked = HumanAsked(
        level="critical",
        question=f"연속 {consecutive}회 에러. 계속할까요?",
        urgency="high",
        response_format="yes_no"
    )
    # ... 사람 응답 대기
```

#### 체크리스트

- [ ] 에러 메시지가 200자 이하로 압축되는가?
- [ ] `_find_resolved_errors()`가 해결된 에러를 건너뛰는가?
- [ ] `count_consecutive_errors()`로 연속 에러를 추적하는가?
- [ ] 연속 에러 N회 이상 시 사람에게 에스컬레이션하는가?

#### 안티패턴

```python
# BAD 1: 에러 전체를 컨텍스트에 넣기
error_msg = traceback.format_exc()  # 수십 줄의 스택 트레이스
events.append(ToolFailed(error_msg=error_msg))

# BAD 2: 해결된 에러를 그대로 두기
# fetch_data 실패 → fetch_data 성공 후에도 에러 메시지가 컨텍스트에 남아있음
# → LLM: "아직 에러가 있네요, 다시 시도합니다" (무한 루프)
```

---

### Factor 10: Small, Focused Agents

#### 원칙 요약

컨텍스트가 커지면 LLM은 길을 잃는다. 하나의 큰 에이전트 대신, 작은 전문 에이전트(3-10 스텝)로 분해하라. Controller가 Sub-Agent에게 위임하는 구조를 사용한다.

#### 왜 중요한가

- 15스텝 이상의 에이전트는 초기 지시를 잊기 시작한다 (컨텍스트 열화)
- 전문 에이전트는 작은 도구 세트와 집중된 프롬프트를 가진다 → 정확도 향상
- 에이전트 간 통신은 Factor 7 (ask_human)과 동일한 패턴이다

#### 구현 패턴

**1) Sub-Agent 명세를 정의한다:**

```python
# agents/registry.py

@dataclass
class SubAgentSpec:
    name: str                               # "monitor", "analyst" 등
    description: str                        # 역할 설명
    system_prompt: str                      # 전용 프롬프트
    tools: dict[str, Callable]              # 전용 도구 세트
    max_steps: int = 8                      # 3-10 스텝 (작게 유지!)

def get_all_agent_specs(snapshot) -> dict[str, SubAgentSpec]:
    return {
        "monitor": SubAgentSpec(
            name="monitor",
            description="데이터 모니터링 + 이상 징후 탐지",
            system_prompt=MONITOR_PROMPT,
            tools=build_monitor_tools(snapshot),
            max_steps=6,
        ),
        "analyst": SubAgentSpec(
            name="analyst",
            description="데이터 분석 + 인사이트 도출",
            system_prompt=ANALYST_PROMPT,
            tools=build_analyst_tools(snapshot),
            max_steps=5,
        ),
        # ... 도메인별 Sub-Agent 추가
    }
```

**2) Controller Agent가 `delegate` 도구로 Sub-Agent에게 위임한다:**

```python
# agents/controller.py

CONTROLLER_PROMPT = """
당신은 총괄 Controller Agent입니다.
직접 작업하지 않고 전문 Sub-Agent에게 위임합니다.

## 사용 가능한 Sub-Agent
- monitor: 데이터 모니터링 + 이상 징후 탐지
- analyst: 데이터 분석 + 인사이트 도출
- reporter: 리포트 생성

## 도구
{"tool": "delegate", "params": {"agent": "monitor", "task": "구체적 작업 지시"}, "reason": "위임 이유"}
{"tool": "done", "params": {"summary": "..."}, "reason": "모든 작업 완료"}
""".strip()
```

**3) Sub-Agent 실행 엔진 — 메인 루프와 동일한 구조:**

```python
# agents/base.py

def run_sub_agent(agent_name, system_prompt, tools, task, 
                  snapshot, config, llm_fn, max_steps=8) -> SubAgentResult:
    """각 Sub-Agent는 독립 run_id, 독립 이벤트 스트림, 독립 컨텍스트를 가진다."""
    run_id = new_run_id()
    events = [TaskStarted(task=task, portfolio_summary=snapshot.to_context_summary())]
    
    for step in range(max_steps):  # 3-10 스텝 제한!
        messages = _derive_with_custom_prompt(events, system_prompt)
        raw_output = llm_fn(messages)
        tool_call = parse_tool_call(raw_output)
        
        if tool_call["tool"] == "done":
            return SubAgentResult(
                agent_name=agent_name, status="done",
                summary=tool_call["params"]["summary"]
            )
        
        # ... 도구 실행 (Sub-Agent 전용 도구 맵 사용)
        handler = tools.get(tool_call["tool"])
        result = handler(tool_call.get("params", {}))
        events.append(ToolSucceeded(tool_name=..., result=result))
    
    return SubAgentResult(agent_name=agent_name, status="max_steps_exceeded")
```

**4) Controller가 delegate 결과를 이벤트로 기록한다:**

```python
# events.py

@dataclass(frozen=True)
class SubAgentStarted(BaseEvent):
    """Controller → Sub-Agent 위임"""
    kind:       Literal["SubAgentStarted"] = "SubAgentStarted"
    agent_name: str = ""
    task:       str = ""

@dataclass(frozen=True)
class SubAgentCompleted(BaseEvent):
    """Sub-Agent → Controller 결과 반환"""
    kind:       Literal["SubAgentCompleted"] = "SubAgentCompleted"
    agent_name: str = ""
    status:     str = ""        # "done" | "failed" | "max_steps_exceeded"
    summary:    str = ""
    sub_run_id: str = ""        # Sub-Agent의 독립 run_id (추적용)
```

#### 체크리스트

- [ ] Sub-Agent의 `max_steps`가 3-10 이내인가?
- [ ] 각 Sub-Agent가 독립된 프롬프트와 도구 세트를 가지는가?
- [ ] Controller가 `delegate` 도구로 위임하는가? (직접 실행하지 않음)
- [ ] Sub-Agent 결과가 `SubAgentCompleted` 이벤트로 기록되는가?

#### 안티패턴

```python
# BAD: 하나의 에이전트에 모든 도구를 다 넣기
agent = Agent(
    tools=[monitor, analyze, trade, rebalance, calculate_tax, news, ...],
    max_steps=50,  # 위험! 컨텍스트 열화
)
# → 15스텝 이후 초기 지시를 잊고 엉뚱한 행동
```

---

### Factor 11: Trigger from Anywhere

#### 원칙 요약

에이전트의 핵심 로직과 트리거 메커니즘을 분리하라. CLI, 웹훅, 크론, Slack 등 어디서든 동일한 에이전트 로직을 호출할 수 있어야 한다.

#### 왜 중요한가

- 에이전트 로직이 특정 트리거에 종속되면 재사용이 불가능하다
- "매일 아침 자동 실행" + "Slack에서 수동 실행" + "웹훅으로 이벤트 기반 실행"을 하나의 코드로

#### 구현 패턴

**1) 진입점과 에이전트 로직을 분리한다:**

```python
# main.py — 진입점 (CLI)
async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="daily_report")
    parser.add_argument("--mode", choices=["controller", "single"], default="controller")
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()
    
    # 데이터 수집 (F13)
    snapshot = await collect_data(args)
    
    # 에이전트 실행 — 트리거와 무관한 순수 함수
    if args.mode == "controller":
        result = run_controller(snapshot=snapshot, task=args.task, config=config, ...)
    else:
        result = run_agent(snapshot=snapshot, task=args.task, config=config, ...)
```

**2) 다른 트리거에서도 동일한 에이전트 로직을 호출:**

```python
# webhook_handler.py — 웹훅 트리거
@app.post("/trigger")
async def handle_webhook(payload: dict):
    snapshot = await collect_data_from_payload(payload)
    result = run_controller(snapshot=snapshot, task=payload["task"], ...)
    return result

# scheduler.py — 크론 트리거
@scheduler.cron("0 9 * * *")
async def daily_check():
    snapshot = await collect_data()
    result = run_controller(snapshot=snapshot, task="daily_report", ...)
    send_notification(result)
```

#### 체크리스트

- [ ] `run_agent()` / `run_controller()`가 트리거 코드와 분리되어 있는가?
- [ ] CLI, 웹훅, 크론 등 최소 2가지 트리거를 지원하거나 쉽게 추가할 수 있는가?
- [ ] 에이전트 로직이 트리거 방식에 의존하지 않는가?

#### 안티패턴

```python
# BAD: CLI와 에이전트 로직이 결합
def main():
    task = input("작업을 입력하세요: ")  # CLI에 종속
    # 에이전트 로직이 여기에 직접 구현
    while True:
        # ... LLM 호출, 도구 실행 ...
        result = input("계속할까요?")  # CLI에 종속
```

---

### Factor 12: Make Your Agent a Stateless Reducer

#### 원칙 요약

에이전트를 Redux의 reducer와 같은 순수 함수로 만들라. `derive_context(events) → messages[]` — 같은 입력이면 항상 같은 출력. 사이드 이펙트 없음. 이것이 모든 Factor의 기반이다.

#### 왜 중요한가

- 순수 함수는 **테스트하기 매우 쉽다** — mock 없이 입력과 출력만 비교
- 같은 이벤트 목록으로 **어느 시점이든 재현** 가능하다 (디버깅)
- LLM 함수를 주입 가능하게 만들면 **API 키 없이 테스트** 가능하다

#### 구현 패턴

**1) `derive_context()`는 순수 함수다:**

```python
# reducer.py — 이 파일의 모든 함수는 순수 함수

def derive_context(events: list, context_format: str = "xml") -> list[dict]:
    """이벤트 목록 → LLM API에 넘길 messages 배열.
    
    이 함수는 완전한 순수 함수:
    - 사이드 이펙트 없음 (DB 접근, 네트워크 호출 없음)
    - 같은 events + 같은 format → 항상 같은 messages
    - 이벤트 목록만 있으면 어느 시점이든 context 재현 가능
    
    Redux 비유:
      Redux:  (state, action) → state
      여기:   (events)        → context (messages[])
    """
    ...
```

**2) LLM 함수를 주입 가능하게 설계한다 (테스트의 핵심):**

```python
# loop.py

# LLM 호출을 추상화 — Callable[[list], str]
LLMCallFn = Callable[[list], str]

def make_anthropic_llm(model: str) -> LLMCallFn:
    """프로덕션용 Claude API 호출 함수"""
    client = anthropic.Anthropic()
    def call(messages):
        response = client.messages.create(model=model, max_tokens=1024, messages=messages)
        return response.content[0].text
    return call

def run_agent(snapshot, task, config, llm_fn: LLMCallFn, ...):
    """llm_fn을 외부에서 주입 — 테스트 시 ScriptedLLM으로 교체"""
    ...
```

**3) ScriptedLLM — API 키 없이 전체 흐름을 테스트:**

```python
# tests/test_integration.py

class ScriptedLLM:
    """미리 정의된 JSON 응답을 순서대로 반환하는 테스트용 LLM"""
    def __init__(self, responses: list[str]):
        self.responses = deque(responses)
    
    def __call__(self, messages: list) -> str:
        return self.responses.popleft()

# 사용 예시
def test_normal_flow():
    llm = ScriptedLLM([
        '{"tool": "fetch_data", "params": {}, "reason": "데이터 조회"}',
        '{"tool": "analyze", "params": {"focus": "risk"}, "reason": "분석"}',
        '{"tool": "done", "params": {"summary": "완료"}, "reason": "종료"}',
    ])
    result = run_agent(snapshot=make_snapshot(), task="analyze",
                       config=AgentConfig(), llm_fn=llm, store=make_store())
    assert result["status"] == "done"
    assert result["steps"] == 3
```

#### 체크리스트

- [ ] `derive_context()`에 사이드 이펙트가 없는가? (DB, 네트워크 호출 없음)
- [ ] `LLMCallFn`이 `Callable[[list], str]`로 추상화되어 있는가?
- [ ] ScriptedLLM으로 전체 흐름을 테스트할 수 있는가?
- [ ] 같은 이벤트 목록을 넣으면 항상 같은 context가 나오는가?

#### 안티패턴

```python
# BAD: derive_context 안에서 DB를 읽거나 API를 호출
def derive_context(events):
    fresh_data = requests.get("https://api.example.com/data")  # 사이드 이펙트!
    # → 같은 events를 넣어도 API 응답에 따라 결과가 달라짐
    # → 테스트 불가능
```

---

### Factor 13 (Appendix): Pre-fetch Context

#### 원칙 요약

에이전트 루프가 시작되기 전에, 필요한 외부 데이터를 미리 수집(pre-fetch)하라. 에이전트가 루프 안에서 API를 호출하는 것보다, 시작 전에 한 번에 수집하는 것이 효율적이고 안정적이다.

#### 왜 중요한가

- 예측 가능한 데이터를 매번 도구 호출로 가져오면 스텝을 낭비한다
- 사전 수집된 데이터를 `TaskStarted` 이벤트에 포함하면, 재개 시에도 데이터가 보존된다
- 에이전트가 "데이터 조회"에 스텝을 쓰지 않고 바로 "분석"부터 시작할 수 있다

#### 구현 패턴

**1) 에이전트 시작 전에 외부 데이터를 병렬 수집한다:**

```python
# main.py

async def collect_snapshot(sources: dict) -> Snapshot:
    """에이전트 루프 시작 전 모든 데이터를 병렬 수집"""
    tasks = []
    if "api_a" in sources:
        tasks.append(fetch_from_api_a(sources["api_a"]))
    if "db" in sources:
        tasks.append(fetch_from_db(sources["db"]))
    
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    items = []
    for source, result in zip(sources.keys(), results):
        if isinstance(result, Exception):
            items.append(DataItem(source=source, error=str(result)))
        else:
            items.append(result)
    
    return Snapshot(timestamp=datetime.now(), items=items)
```

**2) 수집된 데이터를 `TaskStarted` 이벤트에 포함한다:**

```python
start_event = TaskStarted(
    task=task,
    portfolio_summary=snapshot.to_context_summary()  # 사전 수집 데이터
)
```

**3) 에이전트 프롬프트에 사전 수집 데이터가 있음을 명시한다:**

```python
AGENT_PROMPT = """
## 사전 수집 데이터 (Factor 13: Pre-fetch)
필요한 데이터는 이미 컨텍스트에 포함되어 있습니다.
데이터 조회 도구를 다시 호출할 필요 없이, 바로 분석을 시작하세요.
""".strip()
```

#### 체크리스트

- [ ] 에이전트 시작 전에 `collect_snapshot()`이 호출되는가?
- [ ] 수집된 데이터가 `TaskStarted.portfolio_summary`에 포함되는가?
- [ ] 에이전트 프롬프트에 "데이터가 이미 있다"고 명시했는가?
- [ ] 수집 실패 시 에러 정보가 포함되는가? (부분 실패 허용)

#### 안티패턴

```python
# BAD: 에이전트가 루프 안에서 매번 데이터를 조회
# Step 1: LLM → {"tool": "fetch_users"}      ← 스텝 낭비
# Step 2: LLM → {"tool": "fetch_orders"}     ← 스텝 낭비
# Step 3: LLM → {"tool": "analyze"}          ← 이제야 분석 시작
# → 3스텝 중 2스텝을 데이터 수집에 낭비

# GOOD: 사전 수집
# Step 0 (루프 전): users + orders 병렬 수집 → 컨텍스트에 포함
# Step 1: LLM → {"tool": "analyze"}          ← 바로 분석 시작
```

---

## Part 3: Claude Code 활용 가이드

> Claude Code를 사용해서 12-Factor Agent 프로젝트를 처음부터 구축하는 방법.
> 각 단계에서 Claude Code에게 어떤 프롬프트를 주면 효과적인지 실전 예시를 포함한다.

### 3.1 CLAUDE.md 설정

프로젝트 루트에 `CLAUDE.md` 파일을 만들어 Claude Code에게 프로젝트 컨텍스트를 제공한다. 이 파일은 Claude Code가 세션 시작 시 자동으로 읽는다.

```markdown
# CLAUDE.md

## 프로젝트 개요
이 프로젝트는 12-Factor Agents 원칙을 따르는 [도메인] 에이전트입니다.

## 아키텍처 원칙
- 12-Factor Agents (humanlayer/12-factor-agents) 원칙을 엄격히 준수
- LLM 출력은 반드시 JSON (Factor 1)
- 프롬프트는 prompts.py에서 관리 (Factor 2)
- derive_context()는 순수 함수 (Factor 12)
- 도구 호출 = 제안, Selection→Validation→Execution (Factor 4)
- 이벤트 소싱, append-only (Factor 5, 6)

## 파일 구조
- events.py: 불변 이벤트 타입 (frozen dataclass)
- reducer.py: derive_context() 순수 함수
- tools.py: parse_tool_call(), validate_tool_call(), ToolExecutor
- loop.py: 메인 에이전트 루프 (LLMCallFn 주입)
- prompts.py: 모든 프롬프트 문자열
- event_store.py: SQLite append-only 저장소
- agents/: 멀티 에이전트 (controller.py, base.py, registry.py)

## 테스트
- ScriptedLLM 패턴으로 API 키 없이 테스트
- pytest tests/ -v
```

### 3.2 단계별 빌드 워크플로우

12-Factor Agent를 처음부터 구축할 때 권장하는 순서와 각 단계에서 Claude Code에게 줄 프롬프트 예시:

#### Phase 1: 기반 구축 (F5, F6, F12)

이벤트, 리듀서, 이벤트 저장소를 먼저 만든다. 이것이 전체 시스템의 뼈대다.

```
Claude Code 프롬프트 예시:

"12-Factor Agents 원칙에 따라 이벤트 소싱 기반 에이전트를 만들려고 해.
먼저 events.py를 만들어줘.

요구사항:
1. BaseEvent를 frozen dataclass로 정의 (event_id, timestamp 자동 생성)
2. 이벤트 종류: TaskStarted, LLMResponded, ToolSucceeded, ToolFailed,
   ToolRejected, HumanAsked, HumanResponded, AgentCompleted, AgentFailed
3. 각 이벤트에 kind 필드 (Literal 타입)
4. 모든 이벤트의 Union 타입 AgentEvent 정의
5. Factor 4: ToolRejected에 reject_reason, original_params 필드
6. Factor 7: HumanAsked에 level, question, urgency, response_format 필드

이어서 reducer.py도 만들어줘.
핵심: derive_context(events, context_format) → messages[] 순수 함수.
- XML 형식과 평문 형식 둘 다 지원
- Factor 9: _find_resolved_errors()로 해결된 에러 제거
- count_consecutive_errors() 함수
- should_compact() 함수"
```

#### Phase 2: 프롬프트와 도구 (F1, F2, F4)

```
Claude Code 프롬프트 예시:

"prompts.py를 만들어줘.
1. SYSTEM_PROMPT: [도메인] 전문 에이전트의 시스템 프롬프트
   - 사용 가능한 도구를 JSON 예시로 명시 (Factor 1)
   - 규칙: JSON만 출력, 한 번에 하나의 도구, 에러 시 재시도
2. XML_CONTEXT_TEMPLATES: 컨텍스트 형식 템플릿 딕셔너리

이어서 tools.py를 만들어줘.
1. parse_tool_call(llm_output) → dict: 실패 시 ValueError (Factor 7 준수)
2. ValidationResult dataclass
3. validate_tool_call(tool_call, config) → ValidationResult
   - [도메인별 검증 규칙 설명]
4. ToolExecutor 클래스: dispatch(tool_call) → str"
```

#### Phase 3: 에이전트 루프 (F7, F8, F9)

```
Claude Code 프롬프트 예시:

"loop.py를 만들어줘.
핵심 함수: run_agent(snapshot, task, config, llm_fn, human_input_fn, store) → dict

구현 요구사항:
1. LLMCallFn = Callable[[list], str] 타입 정의 (Factor 12)
2. make_anthropic_llm(model) → LLMCallFn 팩토리 함수
3. 메인 루프 구조 (Factor 8의 3가지 패턴):
   - done → return (동기 완료)
   - ask_human → 사람 응답 대기 (비동기 중단)
   - 도구 실행 → continue (다음 스텝)
4. parse_tool_call 실패 시 ToolFailed 이벤트 기록 (Factor 1/7)
5. validate_tool_call → ToolRejected 또는 실행 (Factor 4)
6. 연속 에러 에스컬레이션 (Factor 9)
7. 컨텍스트 압축 체크 (Factor 10)
8. resume_run_id로 재개 + 스냅샷 신선도 검사 (Factor 6)"
```

#### Phase 4: 테스트 (ScriptedLLM 패턴)

```
Claude Code 프롬프트 예시:

"tests/test_integration.py를 만들어줘.
ScriptedLLM 패턴으로 API 키 없이 전체 흐름을 테스트한다.

필요한 테스트:
1. 정상 흐름: fetch → analyze → done (3스텝)
2. 사람 개입: ask_human → 응답 → done
3. 도구 거부: 검증 실패 → ToolRejected → LLM 재시도 → done
4. 연속 에러: 3회 에러 → 에스컬레이션
5. 재개: 이벤트 저장 → 재로드 → 이어서 실행
6. 타임머신: replay_at(run_id, seq)

ScriptedLLM 구현:
class ScriptedLLM:
    def __init__(self, responses: list[str]):
        self.responses = deque(responses)
    def __call__(self, messages):
        return self.responses.popleft()

make_snapshot(), make_store() 공통 픽스처도 만들어줘."
```

#### Phase 5: 멀티 에이전트 (F10)

```
Claude Code 프롬프트 예시:

"멀티 에이전트 아키텍처를 구축해줘. (Factor 10)

1. agents/registry.py:
   - SubAgentSpec dataclass (name, description, system_prompt, tools, max_steps)
   - 에이전트별 프롬프트 (MONITOR_PROMPT, ANALYST_PROMPT 등)
   - 에이전트별 도구 빌더 함수 (build_monitor_tools 등)
   - get_all_agent_specs(snapshot) 함수

2. agents/base.py:
   - run_sub_agent() — 메인 루프와 동일한 구조, 별도 run_id
   - SubAgentResult dataclass (agent_name, status, summary, data)

3. agents/controller.py:
   - CONTROLLER_PROMPT — delegate 도구로 Sub-Agent 위임
   - run_controller() — delegate 처리 + 기타 도구 실행
   - SubAgentStarted/SubAgentCompleted 이벤트 기록"
```

#### Phase 6: 통합 (F11, F13)

```
Claude Code 프롬프트 예시:

"main.py 진입점을 만들어줘.
1. CLI 인자: --task, --mode (controller/single), --auto, --context-format, --resume
2. collect_snapshot(): 외부 데이터 병렬 수집 (Factor 13)
3. mode에 따라 run_controller() 또는 run_agent() 호출
4. 결과 출력: 상태, RunID, 요약"
```

### 3.3 효과적인 Claude Code 대화 기법

#### 원칙 1: 구체적인 아키텍처 제약 조건을 명시하라

```
BAD:  "에이전트를 만들어줘"
GOOD: "12-Factor Agents 원칙에 따라 에이전트를 만들어줘.
       핵심 제약: LLM은 JSON만 출력, derive_context는 순수 함수,
       이벤트는 append-only, 도구 호출은 제안이지 명령이 아님"
```

#### 원칙 2: 기존 코드와의 일관성을 요구하라

```
"기존 events.py의 패턴을 따라서 SubAgentStarted, SubAgentCompleted
이벤트를 추가해줘. frozen dataclass, kind Literal, BaseEvent 상속
패턴을 그대로 사용해."
```

#### 원칙 3: 테스트를 먼저 요청하라

```
"이 기능을 구현하기 전에 먼저 테스트를 작성해줘.
ScriptedLLM 패턴으로 run_sub_agent()의 정상 흐름, 에러 처리,
max_steps 초과 케이스를 테스트하는 코드를 만들어."
```

#### 원칙 4: Factor 번호로 소통하라

```
"이 코드에서 Factor 4 위반을 수정해줘.
validate_tool_call() 없이 바로 dispatch()를 호출하고 있어."
```

#### 원칙 5: 안티패턴을 경고하라

```
"parse_tool_call()에서 JSON 파싱 실패 시
fallback으로 ask_human을 반환하지 마.
Factor 7 위반이야 — 프레임워크가 LLM 대신 판단하면 안 돼.
ValueError를 raise하고, 루프에서 ToolFailed 이벤트로 처리해."
```

#### 원칙 6: 감사(audit) 관점을 요구하라

```
"이 기능에서 감사 추적이 가능한지 확인해줘.
누가(approver), 언제(timestamp), 무엇을(tool_call) 승인했는지
이벤트에 기록되어야 해."
```

### 3.4 디버깅 기법

#### 컨텍스트 디버깅

```python
# 특정 스텝에서 LLM이 실제로 본 컨텍스트를 확인
from loop import replay_at
from event_store import EventStore

store = EventStore()
messages = replay_at("run_abc123", seq=5, store=store)

# messages를 JSON으로 출력하여 확인
import json
print(json.dumps(messages, indent=2, ensure_ascii=False))
```

#### ScriptedLLM으로 재현

```python
# 프로덕션에서 발생한 문제를 ScriptedLLM으로 재현
# 1. 이벤트 스토어에서 LLM 응답을 추출
events = store.load("run_abc123")
llm_responses = [e.raw_output for e in events if hasattr(e, 'raw_output')]

# 2. ScriptedLLM에 주입하여 동일 흐름 재현
llm = ScriptedLLM(llm_responses)
result = run_agent(snapshot=snapshot, task="test", llm_fn=llm, ...)
```

---

## Part 4: 단계별 프로젝트 빌드 가이드

### 4.1 구현 순서 요약

12-Factor Agent를 처음부터 만들 때 권장하는 순서:

```
Phase 1: 뼈대 (Factor 5, 6, 12)
  events.py → reducer.py → event_store.py
  ↓
Phase 2: 프롬프트 + 도구 (Factor 1, 2, 4)
  prompts.py → tools.py → config.py → models.py
  ↓
Phase 3: 에이전트 루프 (Factor 7, 8, 9)
  loop.py
  ↓
Phase 4: 테스트
  tests/test_architecture.py → tests/test_integration.py
  ↓
Phase 5: 멀티 에이전트 (Factor 10)
  agents/registry.py → agents/base.py → agents/controller.py
  ↓
Phase 6: 통합 (Factor 3, 11, 13)
  main.py → data_fetchers/ → context format 확장
  ↓
Phase 7: 프로덕션
  e2e_verify.py → 배포 → 모니터링
```

### 4.2 Phase별 상세

#### Phase 1: 뼈대 — 이벤트 소싱 기반 (30분)

이벤트 타입을 정의하고, 리듀서 순수 함수를 구현하고, 이벤트 저장소를 만든다. 이 3개 파일이 전체 시스템의 기반이며, 나머지 모든 것은 이 위에 쌓인다.

**결과물**: events.py, reducer.py, event_store.py
**검증**: `pytest tests/test_architecture.py` — 리듀서 순수 함수 테스트

#### Phase 2: 인터페이스 — 프롬프트와 도구 (30분)

LLM이 보게 될 프롬프트를 작성하고, 도구 파싱/검증/실행 파이프라인을 구현한다.

**결과물**: prompts.py, tools.py, config.py, models.py
**검증**: `pytest tests/test_architecture.py` — 도구 파싱/검증 테스트

#### Phase 3: 심장 — 에이전트 루프 (30분)

Selection → Validation → Execution 루프를 구현한다. LLMCallFn 주입으로 테스트 가능하게 설계한다.

**결과물**: loop.py
**검증**: `pytest tests/test_integration.py` — ScriptedLLM으로 전체 흐름 테스트

#### Phase 4: 검증 — 테스트 스위트 (30분)

ScriptedLLM 패턴으로 모든 핵심 시나리오를 테스트한다.

**테스트 시나리오 목록**:
| 시나리오 | 검증 내용 |
|---------|----------|
| 정상 흐름 | fetch → analyze → done, 스텝 수 검증 |
| 사람 개입 | ask_human → 응답 → 계속, 이벤트 기록 검증 |
| 도구 거부 | 검증 실패 → ToolRejected → 재시도, 거부 사유 검증 |
| 연속 에러 | N회 에러 → 에스컬레이션, 카운터 검증 |
| 재개 | 저장 → 로드 → 이어서 실행, 스냅샷 신선도 |
| 타임머신 | replay_at(seq) 검증 |
| 컨텍스트 형식 | xml, plain, single 각각 출력 비교 |
| 해결된 에러 제거 | 에러 후 성공 → 에러가 context에서 사라지는지 |

#### Phase 5: 확장 — 멀티 에이전트 (1시간)

Controller + Sub-Agent 아키텍처를 구축한다. Sub-Agent는 독립된 run_id와 이벤트 스트림을 가진다.

**결과물**: agents/registry.py, agents/base.py, agents/controller.py
**검증**: `pytest tests/test_multi_agent.py`

#### Phase 6: 통합 — 진입점과 사전 수집 (30분)

CLI 진입점, 데이터 사전 수집, 컨텍스트 형식 A/B 테스트 기능을 통합한다.

**결과물**: main.py, data_fetchers/
**검증**: `python main.py --task daily_report --mode controller --auto`

#### Phase 7: 프로덕션 — E2E 검증 (30분)

전체 파이프라인을 E2E로 검증하고, 각 Factor 준수 여부를 최종 확인한다.

**E2E 검증 단계**:
1. 이벤트 소싱 → replay 가능한가?
2. 컨텍스트 형식 → xml, plain, single 모두 동작하는가?
3. 에러 처리 → 연속 에러 에스컬레이션, 해결된 에러 제거
4. 멀티 에이전트 → Controller → Sub-Agent → 결과 종합
5. 재개 → 중단 후 이어서 실행, 스냅샷 갱신

### 4.3 12-Factor 준수 최종 체크리스트

| # | Factor | 핵심 질문 | 확인 |
|---|--------|----------|------|
| 1 | Structured Output | LLM이 JSON만 출력하는가? parse 실패 시 예외를 던지는가? | [ ] |
| 2 | Own Prompts | 프롬프트가 prompts.py에서 관리되는가? git 추적되는가? | [ ] |
| 3 | Own Context | derive_context()가 형식을 지원하는가? (xml/plain/single) | [ ] |
| 4 | Tools = Proposals | Selection → Validation → Execution이 분리되어 있는가? | [ ] |
| 5 | Unified State | 이벤트에 실행 상태 + 비즈니스 데이터가 함께 있는가? | [ ] |
| 6 | Launch/Pause/Resume | 이벤트 저장소가 append-only인가? 재개가 가능한가? | [ ] |
| 7 | Contact Humans | ask_human이 도구 호출인가? 프레임워크가 대신 판단하지 않는가? | [ ] |
| 8 | Own Control Flow | 루프가 직접 작성한 for/while인가? 3가지 패턴이 있는가? | [ ] |
| 9 | Compact Errors | 에러가 200자로 압축되는가? 해결된 에러가 제거되는가? | [ ] |
| 10 | Small Agents | Sub-Agent가 3-10 스텝으로 제한되는가? Controller가 위임하는가? | [ ] |
| 11 | Trigger Anywhere | 에이전트 로직이 트리거와 분리되어 있는가? | [ ] |
| 12 | Stateless Reducer | derive_context()가 순수 함수인가? ScriptedLLM 테스트가 있는가? | [ ] |
| 13 | Pre-fetch | 에이전트 시작 전에 데이터를 수집하는가? 프롬프트에 명시했는가? | [ ] |
