# tests/test_native_tools.py — Native Anthropic tool_use 직렬화 검증
#
# make_anthropic_llm 이 tool_use 응답을 기존 JSON-in-text 파이프라인
# (parse_tool_call) 과 호환되게 변환하는지 확인한다. 실제 API 호출은 하지 않고
# Fake 응답 객체로 검증한다.

import json
from types import SimpleNamespace

from loop import _extract_text_from_response
from tools import parse_tool_call


def _fake_response(blocks):
    return SimpleNamespace(content=blocks)


def test_tool_use_block_is_serialized_to_internal_json():
    blocks = [
        SimpleNamespace(type="text", text="포트폴리오를 먼저 조회합니다."),
        SimpleNamespace(
            type="tool_use",
            name="fetch_all_portfolios",
            input={},
            id="t1",
        ),
    ]
    text = _extract_text_from_response(_fake_response(blocks), use_native_tools=True)
    parsed = parse_tool_call(text)
    assert parsed["tool"] == "fetch_all_portfolios"
    assert parsed["params"] == {}
    assert "조회" in parsed["reason"]


def test_tool_use_with_params():
    blocks = [
        SimpleNamespace(
            type="tool_use",
            name="fetch_price_history",
            input={"asset": "FLR", "days": 7},
            id="t2",
        ),
    ]
    text = _extract_text_from_response(_fake_response(blocks), use_native_tools=True)
    parsed = parse_tool_call(text)
    assert parsed["tool"] == "fetch_price_history"
    assert parsed["params"] == {"asset": "FLR", "days": 7}


def test_non_native_path_passes_text_through():
    blocks = [SimpleNamespace(
        type="text",
        text='{"tool":"done","params":{"summary":"완료"},"reason":"끝"}',
    )]
    text = _extract_text_from_response(_fake_response(blocks), use_native_tools=False)
    parsed = parse_tool_call(text)
    assert parsed["tool"] == "done"


def test_tool_schemas_cover_all_executor_handlers():
    """TOOL_SCHEMAS 정의가 ToolExecutor 핸들러 + ask_human/done을 모두 포함."""
    from tool_schemas import TOOL_SCHEMAS
    names = {t["name"] for t in TOOL_SCHEMAS}
    expected = {
        "fetch_all_portfolios", "fetch_price_history", "fetch_defi_yields",
        "analyze_portfolio", "detect_alerts", "generate_report",
        "send_to_notion", "send_telegram_alert",
        "ask_human", "done",
    }
    assert expected.issubset(names), f"누락: {expected - names}"


# ── Phase 5: derive_native_context 구조 검증 ──────────────────────

def test_derive_native_context_pairs_tool_use_and_tool_result():
    """LLMResponded → assistant tool_use, ToolSucceeded → user tool_result 로 매칭."""
    from events import TaskStarted, LLMResponded, ToolSucceeded
    from reducer import derive_native_context

    events = [
        TaskStarted(task="alert_check", portfolio_summary="snapshot"),
        LLMResponded(
            raw_output='{}', tool_name="fetch_all_portfolios",
            tool_params='{}', reason="조회", tool_use_id="tu_abc",
        ),
        ToolSucceeded(tool_name="fetch_all_portfolios", result="총 $10,000"),
    ]
    system, messages = derive_native_context(events)

    assert system  # SYSTEM_PROMPT 가 system= 로 전달됨
    # [0] TaskStarted user text, [1] assistant tool_use, [2] user tool_result
    assert messages[0]["role"] == "user"
    assert messages[0]["content"][0]["type"] == "text"

    assert messages[1]["role"] == "assistant"
    tool_use_block = next(b for b in messages[1]["content"]
                          if b["type"] == "tool_use")
    assert tool_use_block["id"] == "tu_abc"
    assert tool_use_block["name"] == "fetch_all_portfolios"

    assert messages[2]["role"] == "user"
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert messages[2]["content"][0]["tool_use_id"] == "tu_abc"
    assert messages[2]["content"][0]["content"] == "총 $10,000"


def test_derive_native_context_tool_failed_sets_is_error():
    """ToolFailed → tool_result.is_error=True."""
    from events import TaskStarted, LLMResponded, ToolFailed
    from reducer import derive_native_context

    events = [
        TaskStarted(task="t", portfolio_summary="s"),
        LLMResponded(raw_output='{}', tool_name="broken",
                     tool_params='{}', tool_use_id="tu_err"),
        ToolFailed(tool_name="broken", error_type="RuntimeError",
                   error_msg="boom"),
    ]
    _, messages = derive_native_context(events)
    result = messages[-1]["content"][0]
    assert result["type"] == "tool_result"
    assert result["tool_use_id"] == "tu_err"
    assert result["is_error"] is True
    assert "boom" in result["content"]


def test_derive_native_context_closes_orphan_tool_use():
    """
    마지막 LLMResponded 뒤에 결과 이벤트가 없으면 placeholder tool_result 로 닫는다.
    Anthropic API 는 tool_use 없이 assistant 턴이 끝나는 것을 거부한다.
    """
    from events import TaskStarted, LLMResponded
    from reducer import derive_native_context

    events = [
        TaskStarted(task="t", portfolio_summary="s"),
        LLMResponded(raw_output='{}', tool_name="x",
                     tool_params='{}', tool_use_id="tu_x"),
    ]
    _, messages = derive_native_context(events)
    # 마지막이 tool_result 여야 함 (assistant 로 끝나면 API 에러)
    last = messages[-1]
    assert last["role"] == "user"
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["is_error"] is True


def test_derive_native_context_ask_human_gets_tool_result():
    """HumanResponded 는 pending 이 ask_human 일 때만 tool_result 로 닫힌다."""
    from events import TaskStarted, LLMResponded, HumanAsked, HumanResponded
    from reducer import derive_native_context

    events = [
        TaskStarted(task="t", portfolio_summary="s"),
        LLMResponded(raw_output='{}', tool_name="ask_human",
                     tool_params='{"question":"ok?"}', tool_use_id="tu_ask"),
        HumanAsked(question="ok?"),
        HumanResponded(answer="네"),
    ]
    _, messages = derive_native_context(events)
    last = messages[-1]
    assert last["content"][0]["type"] == "tool_result"
    assert last["content"][0]["tool_use_id"] == "tu_ask"
    assert last["content"][0]["content"] == "네"


def test_derive_native_context_skips_legacy_events_without_tool_use_id():
    """tool_use_id 가 비어 있는 레거시 LLMResponded 는 native 경로에서 스킵된다."""
    from events import TaskStarted, LLMResponded
    from reducer import derive_native_context

    events = [
        TaskStarted(task="t", portfolio_summary="s"),
        LLMResponded(raw_output='{}', tool_name="x", tool_params='{}',
                     tool_use_id=""),  # 레거시
    ]
    _, messages = derive_native_context(events)
    # TaskStarted 만 남고 assistant/tool_result 블록 없음
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
