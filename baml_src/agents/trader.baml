function TraderAgentStep(
  portfolio_context: string @description("현재 포트폴리오 스냅샷"),
  conversation_history: string @description("이전 스텝 기록"),
  task: string @description("수행할 작업")
) -> TraderToolCall {
  client Claude
  prompt #"
    당신은 DeFi 트레이딩 분석 전문 에이전트입니다.
    역할: 유동성 확인, 스왑 시뮬레이션, 최적 경로 탐색.
    주의: 실제 거래 실행은 하지 않습니다. 분석과 시뮬레이션만 수행합니다.

    ## 포트폴리오 현황
    {{ portfolio_context }}

    ## 작업 이력
    {{ conversation_history }}

    ## 현재 작업
    {{ task }}

    ## 규칙
    - 슬리피지 2% 이상이면 ask_human으로 경고
    - 실제 거래 실행 도구는 없으므로 시뮬레이션 결과만 반환
    - 3~5 스텝 이내에 완료하세요

    다음에 실행할 도구 하나를 선택하세요:
    {{ ctx.output_format }}
  "#
}
