# ReturnGuard — Adversarial Verification & Remediation Report

Two passes are recorded here:
1. **Verification** (branch `qa/adversarial-verification`, PR #15) — hostile QA → **DO-NOT-SHIP**, 6 defects.
2. **Remediation** (branch `fix/adversarial-defects`) — root-cause fixes, each proven by a test shown to fail pre-fix and pass post-fix; no finding test weakened.

**Environment caveat (unchanged):** Docker (Postgres/Kafka) and a live LLM key are unavailable here. Invariants are made to hold in **both** the offline path (atomic in-memory lock + outbox) and the Postgres path (`UNIQUE` + `ON CONFLICT` + outbox table), proven offline; real-infra runs are **UNVERIFIED with exact commands** (§7), never silently passed.

---

## 1. VERDICT: 🟢 SHIP-CANDIDATE (offline gates met; 2 real-infra confirmations pending)

All 6 defects are closed via minimal, SRS-aligned **product** changes. The ship gate (verification §3) is re-evaluated below — every blocking gate now points at a passing test. The only outstanding items are two **real-infrastructure confirmations** (real-DB concurrency, real cross-process restart) whose *mechanisms* are implemented and proven by offline analogues; run the gated tests in §7 against live services before final production sign-off.

### Before → after
| | Verification pass | Remediation pass |
|---|---|---|
| Verdict | DO-NOT-SHIP | **SHIP-CANDIDATE** |
| Defects open | 6 (1 CRITICAL, 4 HIGH, 1 MEDIUM) | **0** |
| Full suite | 174 passed / **9 failed** | **186 passed / 1 skipped** (gated real-DB test) / 0 failed |
| Eval hard gates | pass (but blind to adversarial vectors) | pass, **and** the adversarial vectors are now covered |
| ruff | clean | clean |

---

## 2. Re-evaluated ship gate (verification §3)

| Gate | Status | Proving test |
|---|---|---|
| 0 CRITICAL | ✅ | D-01 closed |
| 0 HIGH in Safety/Financial/Idempotency/Durability | ✅ | D-02..D-05 closed |
| Guardrail-violation rate 0% (eval) | ✅ | `eval.runner` |
| Satisfaction-floor 100% (eval) | ✅ | `eval.runner` |
| Guardrails reject invalid (negative/over-cap/rounding) amounts | ✅ | `test_negative_*`, `test_guardrail_invariants_hold_for_any_input` |
| Idempotency under **redelivery** | ✅ | `test_redelivery_does_not_double_execute` |
| Idempotency under **concurrency** | ✅ offline + structural; ⏳ real-DB gated | `test_concurrent_identical_request_single_financial_effect`, `test_audit_log_has_atomic_idempotency_guard`, `test_…_real_db` (gated) |
| Paused escalation survives restart & resumes | ✅ proxy; ⏳ real cross-process gated | `test_resume_from_a_fresh_graph_object_sharing_the_checkpointer` |
| One immutable audit row + **guaranteed** event per monetary action | ✅ | `test_emit_failure_leaves_no_orphan_audit_row`, `test_no_orphan_audit_and_every_monetary_has_audit` |
| Risk gate not bypassable by the model | ✅ | `test_malicious_diagnosis_cannot_avoid_risk_escalation`, `test_risk_nuance_is_clamped_to_band` |
| No customer-facing risk leak | ✅ | `test_no_resolution_reply_leaks_risk_internals` |
| Cross-account / risk integrity | ✅ | `test_order_customer_mismatch_does_not_launder_risk`, `test_mismatch_uses_true_owner_and_does_not_leak` |
| Eval gate is not a no-op | ✅ | `test_eval_gate_actually_bites_on_a_broken_guardrail` |

---

## 3. Per-defect: root cause → fix → proof → residual risk

### D-01 (was CRITICAL) — Risk-laundering via order/customer mismatch ✅ FIXED
- **Fix:** `agent/nodes/context.py` now fetches the order first and treats **`order.customer_id` as the authoritative customer** for risk and policy; a supplied id that disagrees is an ownership mismatch that routes to human verification with a named factor. Untrusted input can no longer select whose risk profile applies (NFR-SEC-2, FR-RSK-1).
- **Proof (fails pre-fix / passes post-fix):** `test_order_customer_mismatch_does_not_launder_risk`, `test_mismatch_uses_true_owner_and_does_not_leak`. Also corrected two **accidental owner-mismatches in the eval dataset** (`c12`, `c25`) that the bug had masked.
- **Residual:** none. (Mismatches now always escalate; legitimate matched requests are unaffected.)

### D-03 (was HIGH) — Idempotency not concurrency-safe ✅ FIXED
- **Fix:** `db/schema.sql` + `db/migrations/0001_audit_idempotency_unique.sql` add `UNIQUE(request_id, action_type)`; `repository.record_action` inserts the audit row atomically (in-memory `threading.Lock`; Postgres `INSERT … ON CONFLICT DO NOTHING RETURNING`) and the side effect is applied only if a row was inserted.
- **Proof:** `test_audit_log_has_atomic_idempotency_guard` (structural), `test_concurrent_identical_request_single_financial_effect` (barrier, in-memory), `test_concurrent_identical_request_single_effect_real_db` (gated — §7).
- **Residual:** the real-DB 32-connection race is **UNVERIFIED** here (no Postgres); the `UNIQUE` constraint enforces it atomically by construction.

### D-04 (was HIGH) — Orphan audit on broker failure ✅ FIXED
- **Fix:** transactional outbox — `record_action` writes the audit row **and** the event into `outbox` in one transaction (schema + `0002_outbox.sql`); `events.relay_outbox` publishes pending rows and marks them sent; `emit_event` now calls the broker before recording, so a failure leaves the event safely pending. A broker outage can never leave an audit row with a lost event.
- **Proof:** `test_emit_failure_leaves_no_orphan_audit_row` (now asserts the D-04 invariant: committed audit ⇒ pending outbox ⇒ relayed on recovery), `test_tool_exception_no_partial_effect`.
- **Residual:** an outbox relay worker/loop should run in production (the action path does a best-effort relay; a periodic relay drains anything left pending). Documented, not blocking.

### D-05 (was HIGH, latent) — Unbounded LLM risk nuance ✅ FIXED
- **Fix:** `agent/llm.compute_risk` clamps the model nuance to `±RISK_NUANCE_BAND` **before** combining; `agent/nodes/risk.py` escalates whenever the **deterministic rule score** crosses the threshold, so the model alone can never undo a rule-mandated escalation (NFR-SAF-2).
- **Proof:** `test_malicious_diagnosis_cannot_avoid_risk_escalation`, `test_risk_nuance_is_clamped_to_band`.
- **Residual:** none material.

### D-02 (was HIGH) — Guardrails accept negative amounts ✅ FIXED (+ rounding edge)
- **Fix:** `evaluate_guardrails` rejects `amount < 0`/NaN as `violation`; the final 2-dp rounding is **clamped to the value/cap** so rounding can never push a monetary action over a cap (T-FIN-5 — a `1.375` refund no longer rounds to `1.38 > value`). The decision endpoint additionally rejects a negative `modified_action.amount` and requires a recorded `reviewer_id` (NFR-SEC-4, defence-in-depth; ordered after the 409 paused-check so existing behaviour is preserved).
- **Proof:** `test_negative_refund_must_be_rejected`, `test_negative_coupon_must_be_rejected`, `test_reviewer_modify_negative_amount_does_not_execute`, `test_guardrail_invariants_hold_for_any_input` (Hypothesis, 400 cases — this is what surfaced the rounding edge), `test_decision_endpoint_rejects_negative_modified_amount`.
- **Residual:** none.

### D-06 (was MEDIUM) — Reconciliation undercount ✅ FIXED
- **Fix:** `agent/nodes/logger.py` sets `resolution.amount` to the **sum of all monetary audit components** for the request (primary + goodwill), so it reconciles with the audit rows.
- **Proof:** `test_resolution_amount_reconciles_with_audit_total`.
- **Residual:** `expected_saving`/INR-saved still uses the planner's primary-action estimate (does not subtract the small goodwill sweetener) — a minor analytics under-statement, MEDIUM→LOW, non-blocking.

---

## 4. Strengths preserved (re-run after every fix)
- **Prompt-injection robustness** 12/12 still green; **guardrail upper-bound math** still exact; **§9.3/§9.4** decision logic, e2e matrix, fault degradation, secret hygiene — all unchanged and green. No previously-passing test regressed.

## 5. New artifacts
- Migrations: `db/migrations/0001_audit_idempotency_unique.sql`, `0002_outbox.sql` (reproducible from scratch; `db/schema.sql` is the consolidated baseline).
- New regression tests (each shown to fail pre-fix): `test_mismatch_uses_true_owner_and_does_not_leak`, `test_concurrent_identical_request_single_effect_real_db` (gated), `test_risk_nuance_is_clamped_to_band`, `test_decision_endpoint_rejects_negative_modified_amount`; plus the generalized `test_emit_failure_leaves_no_orphan_audit_row`.

## 6. Master checklist — updated status
| Req | Status |
|---|---|
| FR-CTX consistency (order↔customer) | **PASS** (D-01) |
| FR-GRD-1..4 / NFR-SAF-* (caps, non-bypassable, no negatives, rounding) | **PASS** (D-02, D-05) |
| FR-EXE-1..3 (approved only, idempotent incl. concurrency, safe-fail, no partial) | **PASS** offline; real-DB concurrency gated (D-03, D-04) |
| FR-HIL-3 (modify re-checked; reviewer id; no negative) | **PASS** (D-02) |
| FR-LOG-1..2 / AC-6 (append-only, reconciled, guaranteed event) | **PASS** (D-04, D-06) |
| AC-5 (no double-execute, redelivery + concurrency) | **PASS** offline; real-DB gated |
| AC-4 (restart-resume) | **PASS** proxy; real cross-process gated |
| all other [M] | **PASS** (unchanged from verification §6) |

## 7. Remaining UNVERIFIED — exact run commands (require Docker/LLM)
```bash
# Bring up real infra
docker compose up -d            # postgres(+pgvector) + kafka
psql "$DATABASE_URL" -f db/schema.sql        # or apply db/migrations/000*.sql to an existing DB
make seed && make embed

# AC-5 real-DB concurrency (D-03) — gated test runs once DATABASE_URL_TEST is set
DATABASE_URL_TEST="$DATABASE_URL" pytest tests/concurrency -m concurrency

# AC-5 real two-worker Kafka redelivery
python -m events.consumer &  python -m events.consumer &   # two workers, same group
#   then publish the same request_id twice and assert one audit row + one resolution

# AC-4 real cross-process restart
#   POST /resolve a high-risk case -> it pauses; kill -9 the uvicorn pid; restart `make api`;
#   POST /escalations/{id}/decision -> it resumes from the Postgres checkpointer and completes.

# Real-infra p95 (NFR-PERF-1) and real-LLM behavioural subset (temperature=0)
LLM_PROVIDER=anthropic LLM_API_KEY=... pytest tests/perf tests/e2e -m "perf or e2e"
```
Offline reference numbers (this env): orchestration p50 15 ms / p95 30 ms; suite 186 passed / 1 skipped.

---
*Reproduce:* `pytest tests/` (186 passed, 1 skipped). The fix branch is `fix/adversarial-defects`; the evidence trail is `qa/adversarial-verification` (PR #15).
