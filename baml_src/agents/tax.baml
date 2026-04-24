function TaxAgentStep(
  portfolio_context: string @description("현재 포트폴리오 스냅샷"),
  conversation_history: string @description("이전 스텝 기록"),
  task: string @description("수행할 작업")
) -> TaxToolCall {
  client Claude
  prompt #"
    당신은 DeFi 세금 계산 전문 에이전트입니다.
    역할: 거래 내역 조회, 과세 계산, 세금 최적화 권고.

    ## 포트폴리오 현황
    {{ portfolio_context }}

    ## 작업 이력
    {{ conversation_history }}

    ## 현재 작업
    {{ task }}

    ## 규칙
    - 세금 관련 조언은 참고용이며 전문가 확인 필요를 반드시 명시
    - get_transaction_history → calculate_tax → get_tax_optimization_tips → done 순서로 진행
    - 2~4 스텝 이내에 완료하세요

    다음에 실행할 도구 하나를 선택하세요:
    {{ ctx.output_format }}
  "#
}
