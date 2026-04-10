# context.py — Factor 3: Own Your Context Window + Factor 9: Compact Errors

from prompts import SYSTEM_PROMPT
from models import PortfolioSnapshot


MAX_CONTEXT_MESSAGES = 20   # 컨텍스트 최대 메시지 수
MAX_ERROR_LEN = 200         # 에러 메시지 최대 길이 (Factor 9)


def create_context(snapshot: PortfolioSnapshot, task: str = "daily_report") -> list:
    """초기 컨텍스트 생성 — 포트폴리오 스냅샷을 컨텍스트에 삽입"""
    task_map = {
        "daily_report": "일간 리포트를 생성하고 Notion에 저장해주세요.",
        "alert_check":  "포트폴리오를 점검하고 이상 징후가 있으면 알림을 발송해주세요.",
        "rebalance":    "포트폴리오 리밸런싱이 필요한지 분석하고 권고안을 제시해주세요.",
        "weekly_report":"주간 종합 리포트를 생성해주세요.",
    }
    return [
        {"role": "user", "content":
            f"{snapshot.to_context_summary()}\n\n{task_map.get(task, task)}"}
    ]


def add_assistant_step(context: list, tool_call_json: str) -> list:
    """LLM이 선택한 툴 호출 추가"""
    return context + [{"role": "assistant", "content": tool_call_json}]


def add_tool_result(context: list, tool: str, result: str) -> list:
    """툴 실행 결과 추가"""
    return context + [{"role": "user", "content": f"[{tool} 결과]\n{result}"}]


def add_tool_error(context: list, tool: str, error: Exception) -> list:
    """
    Factor 9: 에러를 컨텍스트에 압축해서 넣기
    — 스택트레이스 전체가 아닌 핵심 정보만
    """
    compressed = f"[{tool} 에러] {type(error).__name__}: {str(error)[:MAX_ERROR_LEN]}"
    return context + [{"role": "user", "content": compressed}]


def add_human_response(context: list, response: str) -> list:
    """사람의 응답 추가 (Factor 7)"""
    return context + [{"role": "user", "content": f"[사용자 응답] {response}"}]


def compact_if_needed(context: list) -> list:
    """
    Factor 9: 컨텍스트가 너무 길면 오래된 툴 결과를 요약으로 압축
    — 최근 N개 메시지 + 압축 요약 유지
    """
    if len(context) <= MAX_CONTEXT_MESSAGES:
        return context

    # 처음 메시지(포트폴리오 데이터)는 반드시 유지
    head = context[:1]
    tail = context[-(MAX_CONTEXT_MESSAGES - 2):]
    compressed_mid = [{
        "role": "user",
        "content": f"[압축] 이전 {len(context) - MAX_CONTEXT_MESSAGES + 1}개 스텝 완료. 계속 진행하세요."
    }]
    return head + compressed_mid + tail


def get_api_messages(context: list) -> list:
    """Anthropic API 호출용 messages 배열 반환 (system 분리)"""
    return [
        {"role": "user",      "content": SYSTEM_PROMPT},
        {"role": "assistant", "content": "네, DeFi 포트폴리오 분석 에이전트로 동작하겠습니다. JSON만 반환합니다."},
    ] + context
