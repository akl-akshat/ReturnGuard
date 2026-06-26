"""Deterministic financial guardrails (SRS §7.7, §9.4, NFR-SAF-1/2) — pure & non-bypassable.

``evaluate_guardrails`` reads **only** the proposed action, order facts, config thresholds,
the risk score, and the customer's recent auto-refund count. It NEVER sees ``raw_request``
or ``messages``, so no request content or model output can flip a guardrail (FR-GRD-4).

Outcome:
* over a *soft* cap but clampable to a policy-valid value → ``clamped`` (recorded).
* over a *hard* auto-execution limit / rate limit / risk gate → ``requires_human``.
* a refund exceeding the order value → ``violation`` (never auto-executed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.decision.cost_model import order_value
from config.settings import settings

FINANCIAL = {"instant_refund", "partial_refund", "store_credit_refund", "retention_coupon", "goodwill_credit"}
REFUNDISH = {"instant_refund", "partial_refund", "store_credit_refund"}


@dataclass
class GuardrailResult:
    status: str  # pass | clamped | violation
    requires_human: bool
    action: dict[str, Any]
    notes: list[str] = field(default_factory=list)


def _worsen(status: str, new: str) -> str:
    order = {"pass": 0, "clamped": 1, "violation": 2}
    return new if order[new] > order[status] else status


def evaluate_guardrails(
    action: dict[str, Any],
    order: dict[str, Any],
    risk_score: float | None,
    auto_refund_count: int,
) -> GuardrailResult:
    notes: list[str] = []
    status = "pass"
    requires_human = False
    at = action.get("action_type", "provide_information")
    amount = float(action.get("amount") or 0.0)
    value = order_value(order) if order else 0.0
    new_action = {**action, "params": dict(action.get("params") or {})}

    # (0) a monetary amount must be non-negative and finite — invalid values never pass (D-02).
    if at in FINANCIAL and (amount < 0 or amount != amount):  # NaN-safe
        status = _worsen(status, "violation")
        requires_human = True
        notes.append(f"invalid monetary amount {amount} rejected")
    if "goodwill" in new_action["params"] and float(new_action["params"]["goodwill"]) < 0:
        new_action["params"]["goodwill"] = 0.0
        status = _worsen(status, "violation")
        requires_human = True
        notes.append("negative goodwill rejected")

    # (1) refund/credit must not exceed the order value — hard violation.
    if at in REFUNDISH and amount > value + 1e-6:
        status = _worsen(status, "violation")
        requires_human = True
        notes.append(f"refund {amount} exceeds order value {value}")

    # (2) coupon caps — clamp to the lesser of pct·value and the absolute cap.
    if at == "retention_coupon":
        cap = min(settings.MAX_COUPON_PCT * value, settings.MAX_COUPON_ABS)
        if amount > cap + 1e-6:
            amount = round(cap, 2)
            new_action["amount"] = amount
            status = _worsen(status, "clamped")
            notes.append(f"coupon clamped to cap {amount}")

    # (3) goodwill caps (both as a primary action and as a defect sweetener).
    if at == "goodwill_credit" and amount > settings.MAX_GOODWILL_CREDIT + 1e-6:
        amount = settings.MAX_GOODWILL_CREDIT
        new_action["amount"] = amount
        status = _worsen(status, "clamped")
        notes.append("goodwill clamped to ceiling")
    if "goodwill" in new_action["params"]:
        g = float(new_action["params"]["goodwill"])
        if g > settings.MAX_GOODWILL_CREDIT + 1e-6:
            new_action["params"]["goodwill"] = settings.MAX_GOODWILL_CREDIT
            status = _worsen(status, "clamped")
            notes.append("goodwill sweetener clamped")

    # (4) auto-execution ceiling — above it, a refund needs a human.
    if at in REFUNDISH and amount > settings.MAX_AUTO_REFUND_ABS + 1e-6:
        requires_human = True
        notes.append(f"refund {amount} over auto ceiling {settings.MAX_AUTO_REFUND_ABS} → human")

    # (5) per-customer auto-refund rate limit.
    if at in REFUNDISH and auto_refund_count >= settings.AUTO_REFUND_RATE_LIMIT:
        requires_human = True
        notes.append(f"auto-refund rate {auto_refund_count} ≥ limit {settings.AUTO_REFUND_RATE_LIMIT} → human")

    # (6) risk escalation gate.
    if (risk_score or 0.0) >= settings.RISK_ESCALATION_THRESHOLD:
        requires_human = True
        notes.append("risk ≥ escalation threshold → human")

    # (7) the escalation action itself.
    if at == "escalate_to_human":
        requires_human = True

    # Final 2-dp rounding must NEVER push a monetary amount over its ceiling (T-FIN-5):
    # round first, then clamp to the value/cap so e.g. round(1.375)=1.38 can't exceed value 1.375.
    if at in FINANCIAL:
        amt = round(amount, 2)
        if at in REFUNDISH:
            amt = min(amt, value)
        elif at == "retention_coupon":
            amt = min(amt, settings.MAX_COUPON_ABS, settings.MAX_COUPON_PCT * value)
        elif at == "goodwill_credit":
            amt = min(amt, settings.MAX_GOODWILL_CREDIT)
        new_action["amount"] = amt
    return GuardrailResult(status=status, requires_human=requires_human, action=new_action, notes=notes)
