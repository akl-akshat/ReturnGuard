"""Evidence assessment — the vision/ML gate (stubbed, with a real seam).

Before ReturnGuard moves any money for a claim that could be fabricated (a "damaged" item,
a "spoiled" meal, a garment that "doesn't fit"), it asks for a photo/video and judges
whether that evidence actually supports the claim. In production a vision model does the
judging; offline we use a deterministic stub — exactly the pattern the codebase already uses
for the LLM (``agent.stub_brain``) and the embedder (``policies.embedder``).

The *gating architecture* is real and is what protects the money: a claim is only
auto-remedied when the assessment ``supports`` it with high confidence; ``inconclusive`` or
``contradicts`` routes to a human. To go live, replace :func:`assess_evidence` with a call to
a real model that returns the same :class:`Assessment` shape.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from config.settings import settings

SUPPORTS = "supports"          # evidence clearly backs the claim → safe to auto-remedy
INCONCLUSIVE = "inconclusive"  # can't tell → a human should look
CONTRADICTS = "contradicts"    # evidence argues against the claim → a human should look

# Confidence (that the claim is TRUE) thresholds. ``SUPPORT_MIN`` is deliberately high — the
# bar is "very, very sure before you act". The *effective* bar adapts to the customer's
# credibility tier (see :func:`support_threshold`): a trusted history relaxes it slightly,
# a poor history tightens it, and a high-risk history never auto-approves at all.
SUPPORT_MIN = 0.85
CONTRADICT_MAX = 0.45

_TIER_THRESHOLDS = {
    "trusted": 0.80,    # good history → smoother, but still a real check every time
    "normal": SUPPORT_MIN,
    "watch": 0.92,      # disproven claims on record → scrutinize harder
    "high_risk": 2.0,   # unreachable → every claim goes to a human
}


def support_threshold(tier: str) -> float:
    """The minimum assessor confidence to auto-accept a claim for this credibility tier."""
    return _TIER_THRESHOLDS.get(tier, SUPPORT_MIN)


@dataclass
class Assessment:
    verdict: str        # supports | inconclusive | contradicts
    confidence: float   # [0,1] model confidence that the claim is genuine
    kind: str           # what was assessed (issue_type)
    detail: str         # short human-readable note


def _hash01(s: str) -> float:
    """Deterministic pseudo-value in [0,1] from a string (stand-in for model output)."""
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def assess_evidence(evidence_ref: str, issue_type: str, category: str,
                    support_min: float = SUPPORT_MIN) -> Assessment:
    """Judge whether the supplied evidence supports the claim — **server-authoritative**.

    The verdict is decided here, never by the caller/customer: there is no client-supplied
    "this is strong" flag to trust (that would let a claimant self-certify and nullify the
    gate). A real deployment replaces this body with a vision-model call on the uploaded media.

    ``support_min`` is the credibility-adaptive bar (see :func:`support_threshold`): the same
    photo can auto-clear for a trusted customer yet route to a human for one on watch.

    Offline (``LLM_PROVIDER=stub``) there is no real model, so the demo simulates one: an
    ``evidence_ref`` beginning ``demo-clear`` scores high and ``demo-blurry`` scores low, so both
    the auto-approve and human-review paths are demonstrable. That simulation is **only** honoured
    in stub mode; in production the prefix is ignored and the deterministic content hash (stand-in
    for the model) decides, so nothing the customer types dictates the verdict.
    """
    ref = (evidence_ref or "").lower()
    if settings.use_stub_llm and ref.startswith("demo-clear"):
        confidence = 0.94
    elif settings.use_stub_llm and ref.startswith("demo-blurry"):
        confidence = 0.28
    else:
        confidence = round(0.30 + 0.65 * _hash01(evidence_ref or issue_type), 3)

    if confidence >= support_min:
        verdict = SUPPORTS
    elif confidence <= CONTRADICT_MAX:
        verdict = CONTRADICTS
    else:
        verdict = INCONCLUSIVE

    return Assessment(
        verdict=verdict,
        confidence=confidence,
        kind=issue_type,
        detail=f"stub-assessor: confidence={confidence:.2f} (bar {support_min:.2f}) on {issue_type}/{category}",
    )
