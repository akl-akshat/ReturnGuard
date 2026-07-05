"""Client representatives + complaint assignment.

Each client brand has support representatives. Escalated complaints (the HITL queue) can be
assigned to an available representative for that brand; the representative logs into their own
portal and sees only the complaints assigned to them. Later this can map to real human agents
with rosters/shifts — the assignment model here is the seam for that.
"""

from __future__ import annotations

import uuid
from typing import Any

from service import chat_store
from service.chat_store import _LOCK, _conn

_INIT_PATH: str | None = None


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS reps (
                id         TEXT PRIMARY KEY,
                company_id TEXT NOT NULL,
                name       TEXT NOT NULL,
                available  INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reps_company ON reps(company_id);
            CREATE TABLE IF NOT EXISTS assignments (
                session_id  TEXT PRIMARY KEY,
                rep_id      TEXT NOT NULL,
                company_id  TEXT NOT NULL,
                assigned_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_assign_rep ON assignments(rep_id);
            """
        )
    _INIT_PATH = chat_store.DB_PATH


def add_rep(company_id: str, name: str) -> dict[str, Any]:
    init()
    rid = "REP-" + uuid.uuid4().hex[:6].upper()
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO reps (id, company_id, name, available, created_at) VALUES (?,?,?,?,?)",
                  (rid, company_id, name.strip(), 1, chat_store._now()))
    return {"id": rid, "company_id": company_id, "name": name.strip(), "available": True}


def get_rep(rep_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT * FROM reps WHERE id=?", (rep_id,)).fetchone()
    return _rep_row(r) if r else None


def reps_for_company(company_id: str) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM reps WHERE company_id=? ORDER BY name", (company_id,)).fetchall()
    return [_rep_row(r) for r in rows]


def set_available(rep_id: str, available: bool) -> None:
    init()
    with _LOCK, _conn() as c:
        c.execute("UPDATE reps SET available=? WHERE id=?", (1 if available else 0, rep_id))


def assign(session_id: str, rep_id: str, company_id: str) -> None:
    init()
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO assignments (session_id, rep_id, company_id, assigned_at) "
                  "VALUES (?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET rep_id=excluded.rep_id, "
                  "assigned_at=excluded.assigned_at",
                  (session_id, rep_id, company_id, chat_store._now()))


def get_assignment(session_id: str) -> dict[str, Any] | None:
    init()
    with _conn() as c:
        r = c.execute("SELECT a.*, r.name AS rep_name FROM assignments a "
                      "JOIN reps r ON r.id=a.rep_id WHERE a.session_id=?", (session_id,)).fetchone()
    return {"session_id": r["session_id"], "rep_id": r["rep_id"], "rep_name": r["rep_name"],
            "company_id": r["company_id"]} if r else None


def sessions_for_rep(rep_id: str) -> list[str]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT session_id FROM assignments WHERE rep_id=?", (rep_id,)).fetchall()
    return [r["session_id"] for r in rows]


def _rep_row(r) -> dict[str, Any]:
    return {"id": r["id"], "company_id": r["company_id"], "name": r["name"],
            "available": bool(r["available"])}
