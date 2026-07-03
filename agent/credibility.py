"""Per-customer credibility — a persisted, mutable "credit score" for claim trustworthiness.

This is distinct from the static seeded fraud/risk signals in :mod:`agent.risk_model`
(return rate, COD refusals, region RTO). Those describe a customer's *profile*; credibility
is *learned* from the outcomes of their claims and changes over time.

Design rules (from the product owner):

* Credibility **never** drops just because a customer asks for a refund.
* Credibility **never** drops for a genuine, evidence-supported claim (it nudges up).
* Credibility drops only when a claim is **disproven** — a human reviewer denies it, or a
  reviewer confirms it was a false/fraudulent claim.
* Low credibility tightens the gates: it adds to the effective risk score, which pushes more
  cases to human review and makes auto-refunds harder to reach.
* It is internal. It is **never** shown to the customer and never mentioned in a reply.

This module is pure logic over a :class:`Credibility` value object; persistence lives in the
durable store (``service.chat_store``), and the read/write is wired at the service layer so
``agent/`` stays free of service-layer imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

# A new customer starts trusted-but-not-blindly; genuine history can raise it, disproven
# claims lower it.
DEFAULT_SCORE = 0.75

# Outcome deltas.
_GENUINE_DELTA = 0.03   # a verified-genuine claim: tiny reward, mostly stays put
_DENIED_DELTA = -0.15   # a human denied the claim (unsupported)
_FALSE_DELTA = -0.30    # a human confirmed the claim was false / fraudulent

# Tiers (used by the gating logic).
TRUSTED = "trusted"
NORMAL = "normal"
WATCH = "watch"
HIGH_RISK = "high_risk"

# How strongly low credibility feeds the effective risk score. At DEFAULT_SCORE the penalty
# is 0; it grows as credibility falls, so a low-credibility customer trips the escalation
# threshold sooner.
_RISK_PENALTY_WEIGHT = 0.7

_OUTCOME_DELTAS = {
    "genuine": _GENUINE_DELTA,
    "denied": _DENIED_DELTA,
    "false_claim": _FALSE_DELTA,
}


@dataclass
class Credibility:
    customer_id: str
    score: float = DEFAULT_SCORE
    genuine_count: int = 0
    denied_count: int = 0
    false_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None, customer_id: str) -> Credibility:
        if not d:
            return cls(customer_id=customer_id)
        return cls(
            customer_id=customer_id,
            score=float(d.get("score", DEFAULT_SCORE)),
            genuine_count=int(d.get("genuine_count", 0)),
            denied_count=int(d.get("denied_count", 0)),
            false_count=int(d.get("false_count", 0)),
        )


def tier(score: float) -> str:
    if score >= 0.80:
        return TRUSTED
    if score >= 0.55:
        return NORMAL
    if score >= 0.30:
        return WATCH
    return HIGH_RISK


def trusts(score: float) -> bool:
    """Whether this customer is credible enough for an evidence-backed auto-remedy."""
    return tier(score) in (TRUSTED, NORMAL)


def risk_penalty(score: float) -> float:
    """Extra risk to add for low credibility (0 at/above the default, growing as it falls)."""
    return round(max(0.0, DEFAULT_SCORE - score) * _RISK_PENALTY_WEIGHT, 4)


def apply_outcome(cred: Credibility, outcome: str) -> Credibility:
    """Return a new Credibility with ``outcome`` applied (genuine | denied | false_claim)."""
    delta = _OUTCOME_DELTAS.get(outcome, 0.0)
    score = max(0.0, min(1.0, round(cred.score + delta, 4)))
    counts = {
        "genuine": cred.genuine_count + (outcome == "genuine"),
        "denied": cred.denied_count + (outcome == "denied"),
        "false_claim": cred.false_count + (outcome == "false_claim"),
    }
    return Credibility(
        customer_id=cred.customer_id,
        score=score,
        genuine_count=counts["genuine"],
        denied_count=counts["denied"],
        false_count=counts["false_claim"],
    )
