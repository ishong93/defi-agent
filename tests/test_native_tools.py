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
