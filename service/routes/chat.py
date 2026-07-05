"""Conversational support endpoints: persistent, multi-turn, stateful, fraud-aware sessions.

Each session is an independent conversation persisted server-side (SQLite), so switching or
reloading never loses history. Replies are generated per message from the actual text +
session state via :mod:`agent.conversation`, which gates every money-moving remedy behind
issue-validity, evidence, credibility, and (for consequential cases) a human review.

This route is the composition point that wires the durable per-customer **credibility** ledger
(kept in :mod:`service.chat_store`) into the otherwise service-free agent layer, and exposes an
operator **review** endpoint that resolves/denies an escalated chat and feeds the outcome back
into credibility.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from agent import conversation, credibility
from agent.deps import get_deps
from service import chat_store, platform_store, policy_store, wallet_store
from tools.actions import execute_action, issue_goodwill_credit

# refund-type actions pay the customer — on the platform they land in the ReturnGuard wallet
_WALLET_ACTIONS = {"instant_refund", "partial_refund", "store_credit_refund", "goodwill_credit"}


def _credit_wallet_if_refund(session: dict, action: dict | None, request_id: str) -> None:
    """A resolved refund for a platform customer credits their wallet (idempotent by request)."""
    if not action or action.get("action_type") not in _WALLET_ACTIONS:
        return
    if not str(session.get("customer_id", "")).startswith("PU-"):
        return
    amount = float(action.get("amount") or 0)
    if amount <= 0:
        return
    company = policy_store.get_company(session["company_id"]) if session.get("company_id") else None
    wallet_store.credit(session["customer_id"], amount, "refund",
                        brand=(company or {}).get("name"),
                        note=f"Refund — {session.get('title', 'order')}", ref=request_id)

router = APIRouter()

# Escalation reasons where a *claim* was assessed and found wanting — a human denial here is a
# genuine disproof and moves credibility. Every other reason is procedural routing (asked for a
# human, declined the remedy, high-value/risk gate) and never penalises credibility on its own.
_ADJUDICABLE_REASONS = {"weak_evidence", "evidence_contradicts"}


class CreateSession(BaseModel):
    customer_id: str = Field(..., min_length=1)
    order_id: str | None = None
    company_id: str | None = None   # bind the session to a tenant's uploaded policy (RAG)


class Evidence(BaseModel):
    # An opaque reference to the uploaded media. The customer CANNOT supply the verdict — the
    # assessor decides server-side (agent.evidence.assess_evidence). See that module for the
    # stub-mode demo simulation (demo-clear-* / demo-blurry-* refs).
    ref: str = Field(..., min_length=1, max_length=200)


class PostMessage(BaseModel):
    text: str = Field(default="", max_length=2000)
    evidence: Evidence | None = None

    @model_validator(mode="after")
    def _need_content(self) -> PostMessage:
        if not self.text.strip() and not self.evidence:
            raise ValueError("text or evidence is required")
        return self


class ReviewDecision(BaseModel):
    decision: Literal["approve", "deny"]
    reviewer_id: str = Field(..., min_length=1, max_length=80)
    fraud: bool = False   # operator explicitly marks a denied claim as fraudulent (harsher credibility hit)
    note: str | None = Field(default=None, max_length=500)


class ClientReply(BaseModel):
    reviewer_id: str = Field(..., min_length=1, max_length=80)
    text: str = Field(..., min_length=1, max_length=1000)


def _resolve_order(order_id: str | None) -> dict | None:
    """An order may live in the seeded core dataset or in a client's platform order DB."""
    if not order_id:
        return None
    return get_deps().data_access.get_order(order_id) or platform_store.get_order(order_id)


def _order_title(order_id: str | None) -> str:
    o = _resolve_order(order_id)
    return o["title"] if o else "New conversation"


def _apply_credibility(customer_id: str, outcome: str) -> None:
    cur = credibility.Credibility.from_dict(chat_store.get_credibility(customer_id), customer_id)
    chat_store.save_credibility(credibility.apply_outcome(cur, outcome).to_dict())


@router.get("/api/sessions")
def list_sessions(customer_id: str) -> list[dict]:
    return chat_store.list_sessions(customer_id)


@router.post("/api/sessions")
def create_session(body: CreateSession) -> dict:
    deps = get_deps()
    # identity: a seeded core customer OR a universal platform user (phone-keyed)
    platform_user = platform_store.get_user(body.customer_id)
    if not platform_user and not deps.repo.get_customer(body.customer_id):
        raise HTTPException(status_code=404, detail="customer not found")

    order = _resolve_order(body.order_id)
    if body.order_id and not order:
        raise HTTPException(status_code=404, detail="order not found")
    company_id = body.company_id
    if order and order.get("company_id"):
        # a client-DB order must belong to this customer and auto-binds the client's policy
        if not platform_user or order.get("phone") != platform_user["phone"]:
            raise HTTPException(status_code=403, detail="order does not belong to this customer")
        company_id = order["company_id"]
    if company_id and not policy_store.get_company(company_id):
        raise HTTPException(status_code=404, detail="company not found")

    sess = chat_store.create_session(body.customer_id, body.order_id, _order_title(body.order_id),
                                     company_id=company_id)
    chat_store.add_message(
        sess["id"], "assistant",
        conversation.greeting(deps, body.customer_id, order,
                              customer_name=(platform_user or {}).get("name")))
    return chat_store.get_session(sess["id"], with_messages=True)


@router.get("/api/sessions/{session_id}")
def get_session(session_id: str) -> dict:
    s = chat_store.get_session(session_id, with_messages=True)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s


@router.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict:
    chat_store.delete_session(session_id)
    return {"ok": True}


@router.post("/api/sessions/{session_id}/messages")
def post_message(session_id: str, body: PostMessage) -> dict:
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")

    # inject the durable credibility score so the agent can gate on it (never shown to the user)
    session["credibility"] = chat_store.get_credibility(session["customer_id"]) or {}

    display = "📎 Shared a photo" if (body.evidence and not body.text.strip()) else body.text
    chat_store.add_message(session_id, "user", display)

    order = _resolve_order(session.get("order_id"))

    # Tenant RAG: embed the query and semantically search the bound company's uploaded policy;
    # the top paragraphs ground this turn's replies and any escalation context.
    policy_ctx = None
    if session.get("company_id") and body.text.strip():
        company = policy_store.get_company(session["company_id"])
        if company:
            q = body.text
            if order:
                q += f" {order['category']}"
            issue = (session.get("state") or {}).get("issue_text")
            if issue:
                q += f" {issue}"
            chunks = policy_store.search(session["company_id"], q)
            if chunks:
                policy_ctx = {"company": company["name"], "chunks": chunks}

    # Real refund history for this customer+order, so status questions get real answers.
    refund_ctx = chat_store.refund_status_for(session["customer_id"], session.get("order_id"))

    evidence = body.evidence.model_dump() if body.evidence else None
    result = conversation.handle_turn(get_deps(), session, body.text, evidence=evidence,
                                      policy_ctx=policy_ctx, order=order, refund_ctx=refund_ctx)

    out_msgs = [chat_store.add_message(session_id, "assistant", m["text"], m["meta"]) for m in result.messages]
    if result.credibility_outcome:
        _apply_credibility(session["customer_id"], result.credibility_outcome)

    title = None
    if result.order_id and session.get("title") in (None, "New conversation"):
        title = _order_title(result.order_id)
    chat_store.update_session(session_id, phase=result.phase, status=result.status,
                              order_id=result.order_id, title=title, state=result.state)

    # auto-resolved refunds for platform customers land in their ReturnGuard wallet
    res = (result.state or {}).get("resolution")
    if result.status == "resolved" and res:
        _credit_wallet_if_refund(session, res, res.get("request_id") or f"{session_id}:auto")

    return {"messages": out_msgs, "phase": result.phase, "status": result.status, "order_id": result.order_id}


# --------------------------------------------------------------- operator review
@router.get("/api/reviews")
def list_reviews(company_id: str | None = None) -> list[dict]:
    """Chat cases awaiting a human decision, with the context the agent gathered.

    ``company_id`` scopes the queue to one client's portal — each brand reviews only the
    escalations on ITS orders/policy."""
    out = []
    for s in chat_store.list_reviews():
        if company_id and s.get("company_id") != company_id:
            continue
        st = s.get("state") or {}
        out.append({
            "id": s["id"], "customer_id": s["customer_id"], "order_id": s.get("order_id"),
            "title": s["title"], "updated_at": s["updated_at"],
            "reason": st.get("escalation_reason"), "issue_type": st.get("issue_type"),
            "root_cause": st.get("root_cause"), "risk_score": st.get("risk_score"),
            "proposed_action": st.get("proposed_action"), "evidence": st.get("evidence"),
            "vendor_notify": bool(st.get("vendor_notify")),
            "policy_company": st.get("policy_company"),
            "policy_citations": st.get("policy_citations"),
            "credibility": chat_store.get_credibility(s["customer_id"]) or {"score": credibility.DEFAULT_SCORE},
        })
    return out


@router.post("/api/sessions/{session_id}/reply")
def client_reply(session_id: str, body: ClientReply) -> dict:
    """A human at the client writes into the SAME customer chat the escalation came from.

    The customer sees the specialist's words in their thread; the case stays open for the
    formal approve/deny decision (which is what moves money and credibility)."""
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.get("status") != "escalated":
        raise HTTPException(status_code=409, detail="session is not with a specialist")
    company = policy_store.get_company(session["company_id"]) if session.get("company_id") else None
    who = f"{company['name']} specialist" if company else "Support specialist"
    msg = chat_store.add_message(session_id, "assistant", f"👤 **{who} ({body.reviewer_id})**: {body.text}",
                                 {"kind": "human_reply", "reviewer_id": body.reviewer_id})
    return msg


@router.post("/api/sessions/{session_id}/review")
def review_session(session_id: str, body: ReviewDecision) -> dict:
    """Operator decision on an escalated chat. Resolves/denies the session and feeds the
    outcome into the customer's durable credibility ledger."""
    session = chat_store.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="session not found")
    if session.get("status") != "escalated":
        raise HTTPException(status_code=409, detail="session is not awaiting a review")

    deps = get_deps()
    st = dict(session.get("state") or {})
    cust = session["customer_id"]
    order = _resolve_order(session.get("order_id"))
    reason = st.get("escalation_reason", "")

    try:
        deps.repo.set_escalation_decided(session_id, body.decision, body.reviewer_id)
    except Exception:  # noqa: BLE001
        pass

    _EXECUTABLE = {"instant_refund", "partial_refund", "store_credit_refund", "retention_coupon",
                   "goodwill_credit", "expedited_replacement", "free_exchange", "exchange_with_size_guide"}
    if body.decision == "approve":
        action = st.get("proposed_action") or {}
        executed = None
        if order and action.get("action_type") in _EXECUTABLE:
            seq = int(st.get("resolve_seq", 0)) + 1
            st["resolve_seq"] = seq
            rid = f"{session_id}:review:{seq}"
            try:
                execute_action(deps.repo, rid, action, order, actor=f"human:{body.reviewer_id}")
                gw = (action.get("params") or {}).get("goodwill")
                if gw:
                    issue_goodwill_credit(deps.repo, rid, order, gw, actor=f"human:{body.reviewer_id}")
                executed = action
            except Exception:  # noqa: BLE001
                executed = None
        msg = ("✅ Good news — a specialist reviewed your case and approved it. "
               + (conversation.result_text(executed, order) if (executed and order)
                  else "We'll action the agreed resolution and follow up by email."))
        card = {"kind": "resolution", "action_type": (executed or {}).get("action_type"),
                "amount": (executed or {}).get("amount") or 0, "pending": False}
        chat_store.add_message(session_id, "assistant", msg, card)
        st["locked"] = True
        chat_store.update_session(session_id, phase="resolved", status="resolved", state=st)
        if executed:
            _credit_wallet_if_refund(session, executed, rid)
        # Reward genuine only when a claim was actually assessed and supported — approving a
        # "customer asked for a human" case (no claim) must not farm credibility upward.
        if (st.get("evidence") or {}).get("verdict") == "supports":
            _apply_credibility(cust, "genuine")
    else:  # deny
        note = f" {body.note}" if body.note else ""
        chat_store.add_message(
            session_id, "assistant",
            "After a specialist review, we're unable to approve this request as the issue couldn't be "
            f"verified against our policy.{note} If you believe this is a mistake, please reply and we'll take "
            "another look.", {"kind": "review", "reason": "denied"})
        st["locked"] = True
        chat_store.update_session(session_id, phase="resolved", status="denied", state=st)
        # Credibility drops ONLY when a human actually disproves a *claim*: the operator marks
        # fraud (harsher), or the escalation was an assessed claim the reviewer rejects. Procedural
        # escalations (asked for a human, declined the remedy, high-value/risk routing) are NOT a
        # disproof, so they leave credibility unchanged.
        if body.fraud:
            _apply_credibility(cust, "false_claim")
        elif reason in _ADJUDICABLE_REASONS:
            _apply_credibility(cust, "denied")

    return chat_store.get_session(session_id, with_messages=True)
