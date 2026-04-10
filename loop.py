# loop.py — Factor 6 + Factor 12 완전 구현 (LLM 주입 가능)

import json
from typing import Callable, Optional
import anthropic

from events import (TaskStarted, LLMResponded, ToolSucceeded,
                    ToolFailed, HumanAsked, HumanResponded,
                    AgentCompleted, AgentFailed)
from reducer import derive_context, should_compact, make_compaction_event
from tools import parse_tool_call, ToolExecutor
from event_store import EventStore
from models import PortfolioSnapshot
from config import AgentConfig
from logger import setup_logger, new_run_id

log = setup_logger("loop")

LLMCallFn = Callable[[list], str]


def make_anthropic_llm(model: str) -> LLMCallFn:
    client = anthropic.Anthropic()
    def call(messages):
        response = client.messages.create(model=model, max_tokens=1024, messages=messages)
        return response.content[0].text
    return call


def run_agent(snapshot, task, config, human_input_fn=None,
              llm_fn=None, resume_run_id=None, store=None):
    if human_input_fn is None:
        human_input_fn = _cli_human_input
    if llm_fn is None:
        llm_fn = make_anthropic_llm(config.model)

    store    = store or EventStore()
    executor = ToolExecutor(snapshot, config)

    if resume_run_id:
        run_id = resume_run_id
        events = store.load(run_id)
        log.info(f"재개: {run_id} ({len(events)}개 이벤트 replay)")
    else:
        run_id = new_run_id()
        store.start_run(run_id, task)
        start_event = TaskStarted(task=task, portfolio_summary=snapshot.to_context_summary())
        store.append(run_id, start_event)
        events = [start_event]
        log.info(f"시작: {run_id} | task={task} | ${snapshot.total_value_usd:,.2f}")

    try:
        for step in range(config.max_steps):
            if should_compact(events):
                compaction = make_compaction_event(events)
                store.append(run_id, compaction)
                events.append(compaction)

            messages   = derive_context(events)
            raw_output = llm_fn(messages)
            tool_call  = parse_tool_call(raw_output)
            tool_name  = tool_call.get("tool", "unknown")

            llm_event = LLMResponded(
                raw_output=raw_output, tool_name=tool_name,
                tool_params=json.dumps(tool_call.get("params", {}), ensure_ascii=False),
                reason=tool_call.get("reason", "")
            )
            store.append(run_id, llm_event)
            events.append(llm_event)
            log.info(f"[{step+1}] {tool_name} — {tool_call.get('reason','')}", extra={"step": step+1, "tool": tool_name})

            if tool_name == "done":
                summary    = tool_call["params"].get("summary", "")
                done_event = AgentCompleted(summary=summary)
                store.append(run_id, done_event)
                events.append(done_event)
                log.info(f"완료: {run_id}")
                return {"status": "done", "run_id": run_id, "summary": summary,
                        "steps": step + 1, "total_events": len(events)}

            if tool_name == "ask_human":
                p        = tool_call.get("params", {})
                level    = p.get("level", "info")
                question = p.get("question", "")
                ctx_info = p.get("context", "")
                asked    = HumanAsked(level=level, question=question, context=ctx_info)
                store.append(run_id, asked)
                events.append(asked)
                answer = human_input_fn(level, question, ctx_info)
                resp   = HumanResponded(answer=answer)
                store.append(run_id, resp)
                events.append(resp)
                continue

            try:
                result   = executor.dispatch(tool_call)
                ok_event = ToolSucceeded(tool_name=tool_name, result=result)
                store.append(run_id, ok_event)
                events.append(ok_event)
                log.debug(f"툴 성공: {tool_name}")
            except Exception as e:
                err = ToolFailed(tool_name=tool_name, error_type=type(e).__name__, error_msg=str(e)[:200])
                store.append(run_id, err)
                events.append(err)
                log.warning(f"툴 실패: {tool_name} — {e}")

        fail = AgentFailed(error="max_steps_exceeded")
        store.append(run_id, fail)
        return {"status": "max_steps_exceeded", "run_id": run_id, "total_events": len(events)}

    except KeyboardInterrupt:
        return {"status": "paused", "run_id": run_id,
                "resume_cmd": f"python main.py --resume {run_id}"}
    except Exception as e:
        store.append(run_id, AgentFailed(error=str(e)))
        log.exception(f"예외: {e}")
        raise


def replay_at(run_id, seq, store=None):
    store  = store or EventStore()
    events = store.load_until(run_id, seq)
    return derive_context(events)


def _cli_human_input(level, question, context=""):
    icon = {"info": "ℹ️", "warning": "⚠️", "critical": "🚨"}.get(level, "❓")
    print(f"\n{icon} [{level.upper()}] {question}")
    if context:
        print(f"   상세: {context}")
    return input("   답변: ").strip()


def _auto_approve_input(level, question, context=""):
    return "자동 승인" if level != "critical" else "자동 승인 불가"
