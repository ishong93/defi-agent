# Harness Engineering 관점의 개선 로그

> Claude 를 쓴다는 것은 "모델"을 쓰는 일이 아니라 **모델을 감싸는 harness** 를
> 쓰는 일이다. harness = 컨텍스트 조립기 + 도구 레지스트리 + 이벤트 스토어 +
> 루프 제어. 성능·정확도·비용은 이 harness 의 품질이 결정한다.

이 문서는 defi-agent 가 Phase 1~5 에 걸쳐 harness 를 어떻게 바꿔왔는지,
각 변경이 harness engineering 축에서 무엇을 개선했는지 정리한다. 단순
"리팩토링 / 성능 튜닝" 체크리스트가 아니라 **LLM 이 실제로 무엇을 보고, 무엇을
기억하고, 어떻게 행동을 다시 계획하는가** 를 개선한 로그다.

## 1. Harness 구성요소와 우리 코드의 매핑

| harness 구성요소 | 이 프로젝트의 구현 | 주요 결정 포인트 |
| ---- | ---- | ---- |
| Context 조립 | `reducer.derive_context` / `reducer.derive_native_context` | 이벤트 → messages 의 순수함수. XML/plain/single/native 포맷 A/B 가능. |
| 도구 레지스트리 | `tools.ToolExecutor` + `tool_schemas.TOOL_SCHEMAS` | `ask_human`, `done` 도 동급 도구로 노출. |
| 이벤트 스토어 | `event_store.EventStore` (SQLite WAL + append-only) | 모든 LLM 호출·도구 실행·사람 응답을 불변 이벤트로 기록. |
| 루프 제어 | `loop.run_agent` | Selection → Validation → Execution 분리 (Factor 4·6·8). |
| 관측 | `LLMResponded.input/output/cache_*_tokens` + 로그 | 토큰·캐시 히트율이 이벤트에 박혀있어 replay 로 사후 계산 가능. |

## 2. 단계별 "무엇이 harness 관점에서 바뀌었나"

### Phase 1 — 속도·비용 1차 최적화

**바뀐 것**
- `EventStore.append_batch` (배치 커밋) + SQLite WAL 모드 활성화.
- `enable_prompt_cache` 플래그 (당시엔 첫 user 메시지에 `cache_control` 을 붙이는
  방식).

**harness 관점**
- **Context 조립 비용 절감**: 이벤트 로그가 커져도 replay·배치 저장이 저렴해짐
  → "이벤트 소싱을 밀어붙여도 디스크가 병목이 안 된다" 는 조건을 확보.
- **입력 토큰 비용 절감**: prompt caching 으로 반복되는 프리픽스를 캐시.

**차선책이었던 이유 (당시 명시 안 한 점 — 보강)**
- 당시 구조는 SYSTEM_PROMPT 를 `system=` 파라미터 대신 "첫 user 메시지 +
  assistant 'ack'" 패턴 (Role Hacking) 으로 주입하고 있었다. 그래서 캐시 대상도
  "첫 user 메시지" 였다. **이는 Role Hacking 구조 위에서만 성립하는 우회책**
  으로, 본질적으로는 Phase 5 에서 `system=` 블록 + `tools=` 마지막 스키마로
  옮겨야 하는 자리였다. Phase 1 에서 그 점을 명시하지 않은 것은 리뷰에서
  정당하게 지적받은 부분이며, 이 문서와 `loop.py` 의 주석으로 보강했다.

### Phase 2 — Factor 3/9 강화

**바뀐 것**
- `context_format` 파라미터 (xml/plain/single) 도입.
- Factor 9 의 "해결된 에러는 context 에서 제거" 규칙 (`_find_resolved_errors`).
- 연속 에러 카운터 (`count_consecutive_errors`) + 사람 에스컬레이션.

**harness 관점**
- **컨텍스트 엔지니어링 실험 가능성**: XML 태그 버전과 평문 버전을 A/B 로
  바꿀 수 있게 됨 → LLM 의 혼동 원인을 실험으로 증명 가능.
- **에러 노이즈 감쇠**: "이미 해결된 실패" 는 다음 호출에서 사라지므로 LLM 이
  과거 실수를 현재 상태로 오인하지 않음.

### Phase 3 — 관측 가능성

**바뀐 것**
- `LLMResponded` 에 `input_tokens / output_tokens / cache_read / cache_creation`
  필드.
- `run_agent` 종료 시 usage 집계 + `cache_hit_pct` 로그.

**harness 관점**
- **토큰·캐시 히트가 이벤트에 박혀있다**: 실시간 관측은 기본이고, 이벤트 로그만
  있으면 사후에도 어떤 run 이 비싸고 어떤 run 이 캐시를 잘 썼는지 해부 가능.

### Phase 4 — Native tool_use 스키마 (부분)

**바뀐 것**
- `tool_schemas.TOOL_SCHEMAS` 정의 + `tools=` 파라미터 전달.
- `_extract_text_from_response` 가 tool_use 블록 → `{tool, params, reason}`
  JSON 으로 직렬화해 기존 `parse_tool_call` 파이프라인과 호환.

**harness 관점 — 왜 이것으로 충분하지 않았나**
- API 레벨에서 스키마 위반은 차단됐지만, **컨텍스트 조립은 여전히 JSON-in-text
  경로** 였다. 즉 모델이 content block 으로 응답해도 우리는 그것을 텍스트로
  바꿔 context 에 꽂았다. `tool_use_id ↔ tool_result` 왕복 구조를 쓰지 않았기
  때문에 모델 입장에선 여전히 "user 가 텍스트로 결과를 보여주는" 모드.

### Phase 5 — 진짜 네이티브 전환 (지금)

**바뀐 것**
- `SYSTEM_PROMPT` 를 `system=` 파라미터로 전달 (Role Hacking 제거).
- `derive_native_context(events) → (system, messages)` 추가: 이벤트 →
  content block 배열. `LLMResponded(tool_use_id)` 가 assistant `tool_use`,
  `ToolSucceeded/Failed/Rejected` 가 user `tool_result` (짝을 맞춘 `tool_use_id`
  로).
- `make_anthropic_llm` 이 `cache_control` 을 **system 블록** 과 **마지막 tool
  스키마** 에 적용 (Phase 1 대비 캐시 프리픽스가 훨씬 커짐).
- `events.LLMResponded.tool_use_id` 필드 추가 (이벤트가 구조화된 ID 까지 품음).

**harness 관점**
- **모델 행동 모델 일치**: Anthropic 의 공식 tool_use 프로토콜을 그대로 사용.
  모델이 학습한 패턴 (tool_use → tool_result) 과 컨텍스트가 일치하므로 지시
  따르기·오류 회복 능력이 모두 향상된다.
- **재생 가능성 강화**: replay 시 `derive_native_context(events[:N])` 만으로
  과거 어느 시점의 API 입력을 그대로 재현 가능. 디버깅·회귀 검증이 단순해짐.
- **캐시 효율 상승**: system (큼) + tools (큼) 이 캐시 프리픽스가 됨. Phase 1
  의 "첫 user 메시지만 캐시" 대비 캐시 가능 토큰 수가 수 배 증가.

## 3. 남아있는 harness 과제

- **컨텍스트 압축의 네이티브 호환**: `derive_native_context` 는 현재
  `ContextCompacted` 를 무시한다. 압축 summary 를 text block 으로 삽입하는
  분기를 추가해야 완전하다.
- **도구 결과의 구조화**: 현재 tool_result 의 content 는 문자열이다. Anthropic
  은 content block 리스트도 받으므로, 큰 JSON 결과는 `{type: "text"}` 여러
  조각으로 쪼개는 것을 검토.
- **비용 관측 SLO**: cache_hit_pct 를 이벤트로 집계만 하고 있다. run 별 최소
  cache_hit_pct 를 CI 에서 강제하는 회귀 가드가 있으면 우회로로 돌아가는
  것을 방지할 수 있다.

## 4. 운영 이슈로 남겨둔 것 (보강)

Phase 1~4 리뷰에서 "E2E 와 단위 테스트의 통합 부재" 가 낮은 만족도 요건으로
지적됐다. Phase 5 에서 `tests/test_e2e_pytest.py` 를 추가해 `e2e_verify.main()`
을 pytest 스위트에서 자동 실행하도록 편입했다. CI 한 번이면 단위·통합·E2E 가
같이 돈다.
