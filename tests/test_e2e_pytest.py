# tests/test_e2e_pytest.py — e2e_verify 를 pytest 스위트로 편입.
#
# 운영 이슈로 지적된 "E2E 테스트와 단위 테스트의 통합 부재" 를 해소한다.
# e2e_verify.main() 은 ScriptedLLM + AsyncMock 으로 네트워크 의존 없이
# 전체 경로 (온체인 수집 → 단일 에이전트 → 도구 거부 → Multi-Agent) 를
# 돌리므로, pytest 한 번으로 단위·통합·E2E 가 모두 회귀 검증된다.

import asyncio
import io
import contextlib
import pytest


@pytest.mark.asyncio
async def test_e2e_verify_runs_end_to_end():
    """e2e_verify.main() 이 예외 없이 완주하고 성공 문자열을 출력해야 한다."""
    from e2e_verify import main

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        await main()
    out = buf.getvalue()

    assert "모든 실행 경로 검증 완료" in out
    assert "단일 에이전트" in out
    assert "Multi-Agent" in out
