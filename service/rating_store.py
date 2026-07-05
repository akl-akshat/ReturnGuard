"""Service ratings + guarded credibility adjustments.

Two trust flows that must not be gameable:

**Company ratings (CSAT).** After a session closes, the customer anonymously rates the
brand's handling 1–5★. A company's public rating is the **credibility-weighted average**:
each rating is weighted by the rater's credit score at the time, so a serial fraudster
(score 0.1) venting one star barely moves Amazon, while a trusted customer's rating counts
in full. One rating per session.

**Customer credibility damage caps.** A brand's denials/fraud reports lower a customer's
credit score — but no single company can destroy it. Negative deltas are logged per
(customer, company, quarter) and clamped to ``QUARTERLY_COMPANY_CAP`` in total: once a
company has taken 0.30 off you this quarter, further reports from *that* company are floored
until the next quarter. Applied automatically on review decisions; the platform admin can
also set a score manually (audited as an event).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from agent import credibility as cred_logic
from service import chat_store
from service.chat_store import _LOCK, _conn

QUARTERLY_COMPANY_CAP = 0.30   # max credibility a single company can remove per quarter
MIN_RATING_WEIGHT = 0.05       # even a zero-score rater leaves a trace, just a tiny one

_INIT_PATH: str | None = None


def init() -> None:
    global _INIT_PATH
    if _INIT_PATH == chat_store.DB_PATH:
        return
    chat_store.init()
    with _LOCK, _conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                session_id TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                company_id TEXT NOT NULL,
                stars      INTEGER NOT NULL,
                weight     REAL NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_rating_company ON ratings(company_id);
            CREATE TABLE IF NOT EXISTS credibility_events (
                id         TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                company_id TEXT,
                delta      REAL NOT NULL,      -- applied (post-cap) delta
                requested  REAL NOT NULL,      -- what the outcome asked for
                reason     TEXT,
                quarter    TEXT NOT NULL,      -- e.g. 2026-Q3
                actor      TEXT,               -- review:<reviewer> | admin:<id> | system
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_credev_cust ON credibility_events(customer_id, quarter);
            """
        )
    _INIT_PATH = chat_store.DB_PATH


def _quarter(ts: float | None = None) -> str:
    t = time.gmtime(ts or time.time())
    return f"{t.tm_year}-Q{(t.tm_mon - 1) // 3 + 1}"


# ------------------------------------------------------------------ CSAT ratings
def rate_session(session_id: str, customer_id: str, company_id: str, stars: int) -> dict[str, Any]:
    """One anonymous rating per closed session, weighted by the rater's current credibility."""
    init()
    stars = max(1, min(5, int(stars)))
    cred = chat_store.get_credibility(customer_id) or {}
    weight = max(MIN_RATING_WEIGHT, float(cred.get("score", cred_logic.DEFAULT_SCORE)))
    with _LOCK, _conn() as c:
        if c.execute("SELECT 1 FROM ratings WHERE session_id=?", (session_id,)).fetchone():
            return {"ok": False, "reason": "already_rated"}
        c.execute("INSERT INTO ratings (session_id, customer_id, company_id, stars, weight, created_at) "
                  "VALUES (?,?,?,?,?,?)",
                  (session_id, customer_id, company_id, stars, round(weight, 4), chat_store._now()))
    return {"ok": True, "stars": stars, "weight": round(weight, 4)}


def has_rating(session_id: str) -> bool:
    init()
    with _conn() as c:
        return bool(c.execute("SELECT 1 FROM ratings WHERE session_id=?", (session_id,)).fetchone())


def company_rating(company_id: str) -> dict[str, Any]:
    """Credibility-weighted average stars for one company."""
    init()
    with _conn() as c:
        rows = c.execute("SELECT stars, weight FROM ratings WHERE company_id=?", (company_id,)).fetchall()
    if not rows:
        return {"rating": None, "count": 0}
    wsum = sum(r["weight"] for r in rows)
    avg = sum(r["stars"] * r["weight"] for r in rows) / wsum if wsum else None
    return {"rating": round(avg, 2) if avg else None, "count": len(rows)}


def all_company_ratings() -> dict[str, dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT company_id, stars, weight FROM ratings").fetchall()
    agg: dict[str, dict[str, float]] = {}
    for r in rows:
        a = agg.setdefault(r["company_id"], {"w": 0.0, "sw": 0.0, "n": 0})
        a["w"] += r["weight"]
        a["sw"] += r["stars"] * r["weight"]
        a["n"] += 1
    return {cid: {"rating": round(a["sw"] / a["w"], 2) if a["w"] else None, "count": int(a["n"])}
            for cid, a in agg.items()}


# ------------------------------------------------------------------ capped credibility
def apply_outcome_capped(customer_id: str, outcome: str, *, company_id: str | None,
                         reason: str | None, actor: str) -> dict[str, Any]:
    """Apply a credibility outcome with the per-company quarterly damage cap.

    Positive outcomes ("genuine") pass through untouched. Negative ones are clamped so the
    company's total damage this quarter never exceeds ``QUARTERLY_COMPANY_CAP``.
    """
    init()
    cur = cred_logic.Credibility.from_dict(chat_store.get_credibility(customer_id), customer_id)
    target = cred_logic.apply_outcome(cur, outcome)
    requested = round(target.score - cur.score, 4)
    applied = requested

    if requested < 0 and company_id:
        q = _quarter()
        with _conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(delta), 0) AS d FROM credibility_events "
                "WHERE customer_id=? AND company_id=? AND quarter=? AND delta < 0",
                (customer_id, company_id, q)).fetchone()
        already = abs(float(row["d"]))
        headroom = max(0.0, QUARTERLY_COMPANY_CAP - already)
        applied = -min(abs(requested), headroom)

    new_score = max(0.0, min(1.0, round(cur.score + applied, 4)))
    chat_store.save_credibility({
        "customer_id": customer_id, "score": new_score,
        "genuine_count": target.genuine_count, "denied_count": target.denied_count,
        "false_count": target.false_count,
    })
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO credibility_events (id, customer_id, company_id, delta, requested, "
                  "reason, quarter, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  ("ce_" + uuid.uuid4().hex[:10], customer_id, company_id, round(applied, 4),
                   requested, reason or outcome, _quarter(), actor, chat_store._now()))
    return {"applied": round(applied, 4), "requested": requested, "score": new_score,
            "capped": abs(applied) < abs(requested) - 1e-9}


def admin_set_score(customer_id: str, score: float, actor: str) -> dict[str, Any]:
    """Manual override by the platform admin (audited as an event)."""
    init()
    cur = chat_store.get_credibility(customer_id) or {"customer_id": customer_id,
                                                      "score": cred_logic.DEFAULT_SCORE,
                                                      "genuine_count": 0, "denied_count": 0,
                                                      "false_count": 0}
    new_score = max(0.0, min(1.0, float(score)))
    delta = round(new_score - float(cur["score"]), 4)
    chat_store.save_credibility({**cur, "customer_id": customer_id, "score": new_score})
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO credibility_events (id, customer_id, company_id, delta, requested, "
                  "reason, quarter, actor, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  ("ce_" + uuid.uuid4().hex[:10], customer_id, None, delta, delta,
                   "admin_manual_set", _quarter(), actor, chat_store._now()))
    return {"score": new_score}


def events_for(customer_id: str, limit: int = 20) -> list[dict[str, Any]]:
    init()
    with _conn() as c:
        rows = c.execute("SELECT * FROM credibility_events WHERE customer_id=? "
                         "ORDER BY created_at DESC LIMIT ?", (customer_id, limit)).fetchall()
    return [{"delta": r["delta"], "requested": r["requested"], "reason": r["reason"],
             "quarter": r["quarter"], "actor": r["actor"], "company_id": r["company_id"],
             "created_at": r["created_at"]} for r in rows]
