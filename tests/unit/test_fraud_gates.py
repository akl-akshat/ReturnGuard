"""Unit tests for the fraud-resistance primitives: issue-validity, evidence assessment,
and the credibility ledger. These are the gates that keep refunds rare and verified."""

import pytest

from agent import credibility, evidence, validity

pytestmark = pytest.mark.unit


# --- validity ---------------------------------------------------------------
def test_size_issue_only_valid_for_wearables():
    assert validity.issue_valid_for_category("wrong_size", "apparel")
    assert validity.issue_valid_for_category("wrong_size", "footwear")
    assert not validity.issue_valid_for_category("wrong_size", "electronics")
    assert not validity.issue_valid_for_category("wrong_size", "grocery")


def test_universal_issues_apply_everywhere():
    for cat in ("electronics", "grocery", "apparel", "books"):
        assert validity.issue_valid_for_category("damaged_item", cat)
        assert validity.issue_valid_for_category("wrong_item", cat)


def test_evidence_required_for_money_moving_claims():
    assert validity.evidence_required("damaged_item", "defect_damage")
    assert validity.evidence_required("wrong_size", "size_fit_mismatch")
    assert validity.evidence_required("wrong_item", "wrong_item_shipped")
    # discretionary / informational claims do not require proof
    assert not validity.evidence_required("late_delivery", "delivery_delay")
    assert not validity.evidence_required("return_request", "changed_mind")


# --- evidence assessor (server-authoritative) -------------------------------
def test_demo_refs_drive_opposite_verdicts_in_stub_mode():
    strong = evidence.assess_evidence("demo-clear-1", "damaged_item", "electronics")
    weak = evidence.assess_evidence("demo-blurry-1", "damaged_item", "electronics")
    assert strong.verdict == evidence.SUPPORTS and strong.confidence >= evidence.SUPPORT_MIN
    assert weak.verdict != evidence.SUPPORTS and weak.confidence <= evidence.CONTRADICT_MAX


def test_assessment_is_deterministic_and_takes_no_client_verdict():
    # the signature has no hint/verdict parameter — the customer cannot self-certify
    a = evidence.assess_evidence("same-ref", "wrong_size", "apparel")
    b = evidence.assess_evidence("same-ref", "wrong_size", "apparel")
    assert a.verdict == b.verdict and a.confidence == b.confidence


# --- credibility ------------------------------------------------------------
def test_credibility_drops_only_on_disproven_claims():
    c = credibility.Credibility("CUST-X")           # starts at DEFAULT_SCORE
    base = c.score
    # merely asking (a genuine, verified claim) never lowers credibility
    up = credibility.apply_outcome(c, "genuine")
    assert up.score >= base and up.genuine_count == 1
    # a human denial lowers it; a confirmed false claim lowers it more
    denied = credibility.apply_outcome(c, "denied")
    false = credibility.apply_outcome(c, "false_claim")
    assert denied.score < base
    assert false.score < denied.score
    assert denied.denied_count == 1 and false.false_count == 1


def test_low_credibility_adds_risk_and_flips_trust():
    assert credibility.risk_penalty(credibility.DEFAULT_SCORE) == 0.0
    assert credibility.risk_penalty(0.2) > 0.0                # low credibility raises effective risk
    assert credibility.trusts(0.9) and credibility.trusts(0.6)
    assert not credibility.trusts(0.25)                      # a watch/high-risk customer isn't auto-trusted


def test_score_clamped_to_unit_interval():
    c = credibility.Credibility("CUST-Y", score=0.02)
    for _ in range(5):
        c = credibility.apply_outcome(c, "false_claim")
    assert 0.0 <= c.score <= 1.0


# --- adaptive scrutiny --------------------------------------------------------
def test_support_threshold_tightens_as_credibility_falls():
    t = evidence.support_threshold
    assert t("trusted") == 0.80                    # good history → smoother
    assert t("normal") == evidence.SUPPORT_MIN
    assert t("watch") == 0.92                      # disproven claims → scrutinize harder
    assert t("high_risk") > 1.0                    # unreachable → always a human


def test_same_evidence_different_bars():
    conf = evidence.assess_evidence("demo-clear-1", "damaged_item", "electronics").confidence  # 0.94
    assert conf >= evidence.support_threshold("watch")        # clears the watch bar
    assert conf < evidence.support_threshold("high_risk")     # never clears high-risk
