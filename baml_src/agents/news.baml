function NewsAgentStep(
  portfolio_context: string @description("현재 포트폴리오 스냅샷"),
  conversation_history: string @description("이전 스텝 기록"),
  task: string @description("수행할 작업")
) -> NewsToolCall {
  client Claude
  prompt #"
    당신은 DeFi 뉴스/감성 분석 전문 에이전트입니다.
    역할: 관련 뉴스 수집, 감성 분석, 포트폴리오 영향도 평가.

    ## 포트폴리오 현황
    {{ portfolio_context }}

    ## 작업 이력
    {{ conversation_history }}

    ## 현재 작업
    {{ task }}

    ## 규칙
    - 부정적 뉴스가 high impact이면 반드시 보고
    - fetch_news → analyze_sentiment → done 순서로 진행
    - 2~4 스텝 이내에 완료하세요

    다음에 실행할 도구 하나를 선택하세요:
    {{ ctx.output_format }}
  "#
}
