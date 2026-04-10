# logger.py — 구조화 로깅 (run_id 기반 전체 추적)

import logging
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from contextvars import ContextVar

# 현재 실행의 run_id를 컨텍스트에 보관 (비동기 환경에서도 안전)
_run_id: ContextVar[str] = ContextVar("run_id", default="no-run")

LOGS_DIR = Path("logs")
LOGS_DIR.mkdir(exist_ok=True)


class JsonFormatter(logging.Formatter):
    """모든 로그를 JSON으로 직렬화 — 로그 수집 도구(CloudWatch, Datadog)와 호환"""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":     datetime.now(timezone.utc).isoformat() + "Z",
            "run_id": _run_id.get(),
            "level":  record.levelname,
            "logger": record.name,
            "msg":    record.getMessage(),
        }
        # 구조화 필드가 있으면 병합 (logger.info("...", extra={"tool": "fetch"}))
        for k, v in record.__dict__.items():
            if k not in logging.LogRecord.__dict__ and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    if log.handlers:
        return log  # 중복 핸들러 방지

    log.setLevel(logging.DEBUG)

    # 콘솔: 사람이 읽기 좋은 포맷
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s — %(message)s", "%H:%M:%S"
    ))

    # 파일: JSON (run_id별 분리)
    fh = logging.FileHandler(LOGS_DIR / "agent.jsonl", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(JsonFormatter())

    log.addHandler(ch)
    log.addHandler(fh)
    return log


def new_run_id() -> str:
    """새 실행 ID 생성 및 컨텍스트에 설정"""
    rid = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    _run_id.set(rid)
    return rid


def get_run_id() -> str:
    return _run_id.get()
