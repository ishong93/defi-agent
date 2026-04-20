# tool_schemas.py — Native Anthropic tool_use 스펙
#
# Factor 4 강화: 프롬프트에 "JSON 형식으로 반환하세요"를 쓰는 대신
# API 레벨에서 구조화된 도구 입력을 강제한다. 스키마 위반 불가.

TOOL_SCHEMAS = [
    {
        "name": "fetch_all_portfolios",
        "description": "전체 포트폴리오 스냅샷 조회 (Flare/XDC/XRP 통합)",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "fetch_price_history",
        "description": "자산의 가격 추이 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "asset": {"type": "string", "enum": ["FLR", "XDC", "XRP"]},
                "days": {"type": "integer", "default": 7},
            },
            "required": ["asset"],
        },
    },
    {
        "name": "fetch_defi_yields",
        "description": "체인별 DeFi 수익률 조회",
        "input_schema": {
            "type": "object",
            "properties": {
                "chain": {"type": "string", "enum": ["Flare", "XDC", "XRP"]},
            },
            "required": ["chain"],
        },
    },
    {
        "name": "analyze_portfolio",
        "description": "포트폴리오 분석 (수익률/위험/리밸런싱 관점)",
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {"type": "string", "enum": ["yield", "risk", "rebalance"]},
            },
            "required": ["focus"],
        },
    },
    {
        "name": "detect_alerts",
        "description": "이상 징후 탐지 및 알림 후보 생성",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "generate_report",
        "description": "포트폴리오 리포트 생성",
        "input_schema": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["daily", "weekly", "alert"]},
            },
            "required": ["type"],
        },
    },
    {
        "name": "send_to_notion",
        "description": "생성된 리포트를 Notion 데이터베이스에 저장",
        "input_schema": {
            "type": "object",
            "properties": {"report_id": {"type": "string"}},
            "required": ["report_id"],
        },
    },
    {
        "name": "send_telegram_alert",
        "description": "텔레그램 채널에 알림 발송",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string"},
                "level": {"type": "string", "enum": ["info", "warning", "critical"]},
            },
            "required": ["message", "level"],
        },
    },
    {
        "name": "ask_human",
        "description": "Factor 7: 사용자에게 확인/승인을 요청하고 루프를 일시 중단",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "string", "enum": ["info", "warning", "critical"]},
                "question": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["level", "question"],
        },
    },
    {
        "name": "done",
        "description": "작업 완료 선언 (루프 종료)",
        "input_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
]
