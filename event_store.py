# event_store.py — Factor 6: Launch / Pause / Resume
#
# 핵심 원리:
#   이벤트는 append-only다. 절대 수정하거나 삭제하지 않는다.
#   재개 = 이벤트 목록을 불러와서 reducer로 context 재구성.
#   롤백 = events[:N] 으로 replay.
#
# 이것이 v2 StateStore와 근본적으로 다른 점:
#   v2: context(결과)를 저장        → 중간 스텝 복원 불가
#   v3: events(원인)를 저장         → 모든 시점 복원 가능

import sqlite3
import json
import dataclasses
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional
from events import (AgentEvent, TaskStarted, SnapshotRefreshed,
                    LLMResponded, ToolRejected, ToolSucceeded,
                    ToolFailed, HumanAsked, HumanResponded, ContextCompacted,
                    AgentCompleted, AgentFailed)

DB_PATH = Path("state/events.db")

# 이벤트 kind → 클래스 매핑
EVENT_CLASSES = {
    "TaskStarted":       TaskStarted,
    "SnapshotRefreshed": SnapshotRefreshed,
    "LLMResponded":      LLMResponded,
    "ToolRejected":      ToolRejected,
    "ToolSucceeded":     ToolSucceeded,
    "ToolFailed":        ToolFailed,
    "HumanAsked":        HumanAsked,
    "HumanResponded":    HumanResponded,
    "ContextCompacted":  ContextCompacted,
    "AgentCompleted":    AgentCompleted,
    "AgentFailed":       AgentFailed,
}


class EventStore:
    """
    Append-only 이벤트 저장소.
    한 번 쓴 이벤트는 절대 수정/삭제 불가.

    Factor 6 구현:
      append(run_id, event)    → 이벤트 추가
      load(run_id)             → 이벤트 목록 반환 (replay 가능)
      load_until(run_id, step) → step번째까지만 (롤백용)
    """

    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id     TEXT NOT NULL,
                seq        INTEGER NOT NULL,      -- 실행 내 순서 (0부터)
                kind       TEXT NOT NULL,          -- 이벤트 타입
                event_id   TEXT NOT NULL,
                timestamp  TEXT NOT NULL,
                payload    TEXT NOT NULL,          -- JSON (kind 제외 필드)
                UNIQUE(run_id, seq)
            );
            CREATE TABLE IF NOT EXISTS runs (
                run_id     TEXT PRIMARY KEY,
                task       TEXT NOT NULL,
                status     TEXT NOT NULL DEFAULT 'running',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_run_seq ON events(run_id, seq);
        """)
        self.conn.commit()

    # ── 쓰기 (append-only) ────────────────────────────────────────

    def start_run(self, run_id: str, task: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?)",
            (run_id, task, "running", now, now)
        )
        self.conn.commit()

    def append(self, run_id: str, event: AgentEvent) -> int:
        """
        이벤트를 저장소에 추가.
        반환값: 이 이벤트의 seq 번호 (롤백 시 사용)
        """
        # 현재 마지막 seq 조회
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()
        seq = row[0] + 1

        # 이벤트 → JSON (kind, event_id, timestamp 제외)
        d = dataclasses.asdict(event)
        payload = {k: v for k, v in d.items()
                   if k not in ("kind", "event_id", "timestamp")}

        self.conn.execute(
            "INSERT INTO events(run_id,seq,kind,event_id,timestamp,payload) VALUES(?,?,?,?,?,?)",
            (run_id, seq, event.kind, event.event_id,
             event.timestamp, json.dumps(payload, ensure_ascii=False))
        )

        # runs 테이블 상태 갱신
        status = (
            "done"   if isinstance(event, AgentCompleted) else
            "failed" if isinstance(event, AgentFailed)    else
            "paused" if isinstance(event, HumanAsked)     else
            "running"
        )
        self.conn.execute(
            "UPDATE runs SET status=?, updated_at=? WHERE run_id=?",
            (status, datetime.now(timezone.utc).isoformat(), run_id)
        )
        self.conn.commit()
        return seq

    # ── 읽기 ──────────────────────────────────────────────────────

    def load(self, run_id: str) -> list[AgentEvent]:
        """전체 이벤트 목록 반환 (replay 가능)"""
        return self._load_range(run_id, 0, None)

    def load_until(self, run_id: str, seq: int) -> list[AgentEvent]:
        """seq번째까지만 반환 — 롤백/포인트 인 타임 재현"""
        return self._load_range(run_id, 0, seq)

    def _load_range(self, run_id: str, from_seq: int, to_seq: Optional[int]) -> list[AgentEvent]:
        if to_seq is None:
            rows = self.conn.execute(
                "SELECT kind,event_id,timestamp,payload FROM events WHERE run_id=? AND seq>=? ORDER BY seq",
                (run_id, from_seq)
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT kind,event_id,timestamp,payload FROM events WHERE run_id=? AND seq>=? AND seq<=? ORDER BY seq",
                (run_id, from_seq, to_seq)
            ).fetchall()

        events = []
        for kind, event_id, timestamp, payload_json in rows:
            cls = EVENT_CLASSES.get(kind)
            if cls:
                payload = json.loads(payload_json)
                events.append(cls(event_id=event_id, timestamp=timestamp, **payload))
        return events

    def get_run_status(self, run_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM runs WHERE run_id=?", (run_id,)
        ).fetchone()
        return row[0] if row else None

    def list_resumable(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT run_id, task, status, updated_at FROM runs WHERE status IN ('running','paused') ORDER BY updated_at DESC"
        ).fetchall()
        return [{"run_id":r[0],"task":r[1],"status":r[2],"updated_at":r[3]} for r in rows]

    def count_events(self, run_id: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
