"""Deterministic risk scoring (the rules half of the hybrid assessor, FR-RSK-1/2).

Pure function over the fraud signals → ``(score in [0,1], named factors)``. The Risk
Assessor node combines this with an LLM nuance pass; keeping the math here makes it
unit-testable and keeps the score reproducible.
"""

from __future__ import annotations

from typing import Any

# Weights chosen so a serial-returning, COD-refusing, high-value profile clears the
# escalation threshold while an ordinary low-return customer stays well below it.
W_RETURN_RATE = 0.40
W_COD_REFUSAL = 0.20
W_REGION_RTO = 0.15
W_CATEGORY = 0.10
B_HIGH_VALUE = 0.15
B_WARDROBING = 0.10
B_SERIAL = 0.10
B_COD_REFUSER = 0.05


def score_and_factors(signals: dict[str, Any]) -> tuple[float, list[str]]:
    score = 0.0
    factors: list[str] = []

    rr = float(signals.get("return_rate", 0.0))
    score += W_RETURN_RATE * rr
    if rr >= 0.45:
        factors.append(f"high_return_rate({rr:.2f})")

    cod = float(signals.get("cod_refusal_rate", 0.0))
    score += W_COD_REFUSAL * cod
    if cod >= 0.30:
        factors.append(f"cod_refusal_history({cod:.2f})")

    rto = float(signals.get("region_rto_baseline", 0.15))
    score += W_REGION_RTO * rto
    if rto >= 0.40:
        factors.append("high_rto_region")

    cap = float(signals.get("category_abuse_propensity", 0.3))
    score += W_CATEGORY * cap
    if cap >= 0.50:
        factors.append(f"abuse_prone_category({signals.get('category')})")

    if signals.get("high_value_order"):
        score += B_HIGH_VALUE
        factors.append(f"high_order_value({signals.get('order_value')})")
    if signals.get("wardrobing_suspected"):
        score += B_WARDROBING
        factors.append("wardrobing_pattern")

    flags = signals.get("risk_flags", []) or []
    if "serial_returner" in flags:
        score += B_SERIAL
        if "wardrobing_pattern" not in factors:
            factors.append("serial_returner")
    if "cod_refuser" in flags:
        score += B_COD_REFUSER

    score = max(0.0, min(1.0, round(score, 4)))
    if not factors:
        factors.append("nominal_profile")
    return score, factors
