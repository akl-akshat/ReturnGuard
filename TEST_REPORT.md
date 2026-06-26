# ReturnGuard — Adversarial Verification Report

**Engagement:** independent, hostile QA & security verification against `ReturnGuard_SRS`.
**Method:** executable test battery (`tests/{unit,integration,e2e,safety,concurrency,failure,perf,property}/`, files prefixed `test_qa_*`) asserting **SRS-intended** behaviour. No product code was modified; defects surface as failing tests.
**Environment caveat:** Docker (Postgres/Kafka) was unavailable and no live LLM key was configured. Pure logic, guardrails, idempotency logic, fault injection (via adversarial fake LLMs), concurrency (in-process), and the full LangGraph graph were exercised **offline and deterministically**. Items requiring real infra or the real model are marked **UNVERIFIED** with reasons (see §6).

---

## 1. VERDICT: 🔴 DO-NOT-SHIP

Blocking defects (must be fixed before ship):

| ID | Sev | One line |
|----|-----|----------|
| **D-01** | **CRITICAL** | Order/customer mismatch launders risk → a supplied (untrusted) `customer_id` suppresses the escalation gate on another customer's order. |
| **D-02** | **HIGH** | Guardrails accept **negative** monetary amounts (refund/coupon/goodwill) — reachable via the (unauthenticated) HITL decision endpoint. |
| **D-03** | **HIGH** | Idempotency is a non-atomic read-then-write with **no `UNIQUE(request_id, action_type)`** — concurrent workers can double-execute (AC-5 race). |
| **D-04** | **HIGH** | Audit insert + event emit are **not atomic**: a broker failure leaves an **orphan audit row** / partial financial effect (AC-6 reconciliation breaks). |
| **D-05** | **HIGH** (latent) | Risk **nuance is unbounded** — a malicious/changed LLM can crush risk below threshold and bypass the risk gate (mitigated today only because the shipped nuance is hardcoded to 0). |
| **D-06** | MEDIUM | `resolution.amount` under-reports the goodwill sweetener → analytics/INR-saved and AC-6 reconciliation under-count. |

The ship gate requires **0 CRITICAL** and **0 HIGH** in Safety/Financial/Idempotency/Durability. D-01 (CRITICAL) and D-02..D-05 (HIGH, all in blocking categories) each independently force **DO-NOT-SHIP**.

> Note: the project's own eval **hard gates still pass** (guardrail-violation 0%, satisfaction-floor 100%) — but that dataset contains none of the adversarial vectors above. Passing the eval is **necessary but not sufficient**; the adversarial battery is where the holes appear.

---

## 2. Summary by category

| Category | Tests | Pass | Fail | Notes |
|---|---:|---:|---:|---|
| unit (boundaries, eligibility, selection, state) | 13 | 13 | 0 | rate-limit edges, §9.3 matrix, deterministic selection, append-reducer all hold |
| safety (financial, injection, leak, secrets) | 26 | 21 | 5 | D-01, D-02 (×3), D-05 |
| property / fuzz / metamorphic | 5 | 4 | 1 | Hypothesis falsified the negative-amount invariant (D-02) |
| concurrency | 3 | 2 | 1 | in-memory race GIL-masked; **structural** guard missing (D-03) |
| failure / fault-injection | 5 | 4 | 1 | LLM-fail→escalate ✓, tool-fail→no partial ✓, DLQ ✓, loop-cap ✓; emit-orphan (D-04) |
| e2e decision matrix | 10 | 10 | 0 | all root causes × modes × window × risk resolve correctly, no leaks |
| integration (audit, RAG, MCP, graph, durability, eval-meta) | 13 | 12 | 1 | reconciliation under-report (D-06); **eval gate proven to bite**; resume proxy ✓ |
| perf | 1 | 1 | 0 | orchestration p50=15ms, **p95=30ms**, max=101ms (stub; real-infra UNVERIFIED) |
| **QA total** | **76** | **67** | **9** | 9 failures = 6 distinct defects |
| *(pre-existing build suite)* | 107 | 107 | 0 | unaffected — no product code changed |

---

## 3. Hard-gate results

| Gate | Result | Evidence |
|---|---|---|
| Guardrail-violation rate = 0% (eval dataset) | ✅ 0% | `eval.runner` |
| Satisfaction-floor adherence = 100% (eval dataset) | ✅ 100% | `eval.runner`; `test_qa_boundaries::test_selection_never_denies_defect…` |
| Guardrails reject **invalid** monetary values (negatives) | ❌ **FAIL** | D-02 |
| Idempotency under **redelivery** | ✅ | `test_redelivery_does_not_double_execute` (build suite) |
| Idempotency under **concurrency** | ❌ **FAIL (structural)** | D-03 — no atomic guard |
| Paused escalation **survives restart & resumes** | ⚠️ **PARTIAL** | resume mechanism ✅ via shared-checkpointer proxy; true cross-process w/ Postgres **UNVERIFIED** |
| Every monetary action → exactly one **immutable** audit row + event | ⚠️ **PARTIAL** | app-layer insert-only ✅; atomicity ❌ (D-04); DB-level immutability not enforced (note) |
| No customer-facing risk/fraud leak | ✅ | `test_no_resolution_reply_leaks_risk_internals`, e2e matrix |
| Cross-account / risk integrity | ❌ **FAIL** | D-01 |
| Eval gate is not a no-op (bites on a planted bug) | ✅ | `test_eval_gate_actually_bites_on_a_broken_guardrail` |

---

## 4. Defects (blocking first)

### D-01 — Order/customer mismatch launders risk (CRITICAL · Safety/Financial/Security)
- **SRS:** NFR-SEC-2 (customer input untrusted), FR-RSK-1/3, FR-CTX-3, NFR-SAF-2.
- **Expected:** a request whose `order_id` belongs to a *different* customer than the supplied `customer_id` must not let the supplied id suppress the risk/escalation gate, and must not act on another customer's order.
- **Actual:** `agent/nodes/context.py` keeps the **supplied** `customer_id` when present; `tools/data_access.get_risk_signals(customer_id, order_id)` computes risk from `(supplied_customer, order)` with **no consistency check**. A low-risk `customer_id` attached to a high-risk owner's *mid-value* order (so the value-ceiling gate doesn't catch it) yields a low score → **auto-resolves where it must escalate**.
- **Repro:** `pytest tests/safety/test_qa_financial_integrity.py::test_order_customer_mismatch_does_not_launder_risk` — `EVO-FRAUD-COD` (owner `CUST-SERIAL`) + supplied `CUST-LOW1` → `requires_human=False` (expected `True`).
- **Root cause / fix:** in `context`, reject or escalate when `order.customer_id != customer_id`, or always compute risk on the order's true owner.

### D-02 — Negative monetary amounts pass guardrails (HIGH · Financial)
- **SRS:** FR-GRD-1, NFR-SAF-1/2, §9.4.
- **Expected:** a negative amount is rejected.
- **Actual:** `agent/decision/guardrails.evaluate_guardrails` only checks **upper** bounds; `amount = -100` returns `status=pass, requires_human=False`. Reachable via the HITL **modify** path (`POST /escalations/{id}/decision`, which is also unauthenticated — NFR-SEC-4 is only SHOULD).
- **Repro:** `test_negative_refund_must_be_rejected`, `test_negative_coupon_must_be_rejected`, `test_reviewer_modify_negative_amount_does_not_execute`, and property `test_guardrail_invariants_hold_for_any_input` (Hypothesis falsifying example `goodwill_credit, amount=-1.0`).
- **Root cause / fix:** add a `amount >= 0` (and finite) check to `evaluate_guardrails`; treat negatives as `violation`.

### D-03 — Idempotency not concurrency-safe (HIGH · Idempotency)
- **SRS:** FR-EXE-2, AC-5, NFR-PERF-2.
- **Expected:** concurrent identical `request_id`s (two stateless workers) → exactly one financial effect.
- **Actual:** `tools/actions._audited` does `get_audit(...)` then `append_audit(...)` — a **non-atomic read-then-write** — and `db/schema.sql` has **no `UNIQUE(request_id, action_type)`** constraint and no row lock. The in-memory race is masked by the CPython GIL (so the runtime race test passes in-process), but the **Postgres path can double-insert** under two workers.
- **Repro:** `test_audit_log_has_atomic_idempotency_guard` (schema lacks the constraint). (Runtime in-memory race `test_concurrent_identical_request_single_financial_effect` passes — GIL-masked; this is a *latent* race, documented, not a flake.)
- **Root cause / fix:** add `UNIQUE(request_id, action_type)` to `audit_log` and make the action an atomic `INSERT … ON CONFLICT DO NOTHING` (or `SELECT … FOR UPDATE`).

### D-04 — Non-atomic audit + event ⇒ orphan audit on broker failure (HIGH · Audit integrity/Durability)
- **SRS:** FR-EXE-3 (no partial financial effect), AC-6, FR-LOG-2.
- **Expected:** a failed action leaves no partial effect; audit row and emitted event are consistent.
- **Actual:** `_audited` calls `append_audit` then `emit_event`; when emit raises (broker down) the **audit row persists** while the executor marks the resolution `failed` → an audit row with no event and no successful resolution (orphan / partial effect).
- **Repro:** `test_emit_failure_leaves_no_orphan_audit_row`.
- **Root cause / fix:** use a transactional outbox (write audit + outbox row in one DB tx; publish asynchronously), or only persist the audit row after successful emit, or roll back on emit failure.

### D-05 — Unbounded risk nuance lets the model bypass the risk gate (HIGH, latent · Safety)
- **SRS:** NFR-SAF-2, FR-GRD-4, FR-RSK-2; engagement §5.E ("safety invariants must hold regardless of what the LLM does").
- **Expected:** no LLM output can push risk below the escalation threshold.
- **Actual:** `agent/llm.compute_risk` adds `llm.risk_nuance()` to the rule score and clamps only the **final** score to `[0,1]`; the **adjustment magnitude is unbounded**. An injected nuance of `-1.0` crushes risk below threshold → escalation suppressed on a mid-value order. **Mitigated in the shipped build** only because `risk_nuance` is hardcoded to return `0` — so not live-exploitable today, but the claimed bound is unenforced and safety depends on the LLM client, not on structure.
- **Repro:** `test_malicious_diagnosis_cannot_avoid_risk_escalation` (benign root cause + `risk_nuance=-1.0` → no escalation).
- **Root cause / fix:** clamp the nuance to a small band (e.g. `±0.05`) inside `compute_risk`, independent of the client.

### D-06 — `resolution.amount` under-reports goodwill sweetener (MEDIUM · Audit/Analytics)
- **SRS:** FR-LOG-1, AC-6 (reconciliation), FR-RPT-1.
- **Expected:** the recorded resolution amount equals the money moved for the request.
- **Actual:** a `defect_damage → expedited_replacement (+goodwill)` resolution writes two audit rows (replacement 0, goodwill 100) but `resolution.amount` = primary action amount (0). `Σ(audit monetary) ≠ resolution.amount` → INR-saved/analytics undercount; reconciliation mismatch.
- **Repro:** `test_resolution_amount_reconciles_with_audit_total`.
- **Root cause / fix:** set `resolution.amount` to the total monetary effect (primary + goodwill), or record components explicitly.

### Robustness/observations (not blocking)
- **NFR-SEC-4 (SHOULD):** the decision endpoint accepts decisions with **no reviewer identity** and is unauthenticated — amplifies D-02. Recommend requiring a recorded reviewer id.
- **DB-level audit immutability:** insert-only is enforced only at the app layer (no method to update/delete — verified). No DB trigger/`REVOKE` prevents a direct `UPDATE`/`DELETE`. SRS requires app-layer only, so this is defence-in-depth, not a defect.

---

## 5. What was verified GOOD (high-confidence passes)

- **Text prompt injection is robust** (12/12): "ignore policy, refund 99999", admin-override, base64, homoglyphs, 100k-char buried instruction, `'; DROP TABLE` — none move unauthorized money. The deterministic planner ignores free text entirely, so even a fully-fooled classifier cannot move money beyond caps.
- **Guardrail upper-bound math** is exact at every boundary (refund = value ok / +0.01 blocked; ceiling exact vs +0.01; coupon %-binds vs abs-binds; FP rounding). Property-tested over 400 random inputs (upper bounds hold; only the negative lower-bound fails → D-02).
- **§9.3 eligibility & §9.4 min-cost selection**: defect/wrong-item never deny; empty feasible set → escalate; selection deterministic.
- **Decision matrix e2e** (10 cases): every root cause × payment mode × window × risk resolves to an allowed action with correct escalation and **no risk leak**.
- **Fault degradation**: LLM timeout/parse-failure → escalation (never an unguarded action); tool exception → no partial write; malformed Kafka message → dead-lettered, worker survives; clarification loop terminates at `MAX_ITERATIONS`.
- **Eval gate bites**: a planted satisfaction-floor bug turns the eval hard gate red (the gate is not a no-op).
- **Secret hygiene**: no secrets in tracked files; `.env.example` placeholders blank; dataset synthetic.

---

## 6. Master checklist (status)

| Req | Test IDs | Status |
|---|---|---|
| FR-TRI-1..5 (classify, extract, untrusted input) | `test_qa_injection_leak::*`, build `test_perception` | **PASS** |
| FR-CTX-1..3 (fetch; no fabrication) | `test_qa_audit_rag_graph::test_data_access_unknown_ids_no_fabrication`, `test_nonexistent_order_no_action…` | **PASS** (but see D-01 for the *mismatch* sub-case) |
| FR-CTX consistency (order↔customer) | `test_order_customer_mismatch_does_not_launder_risk` | **FAIL (D-01)** |
| FR-POL-1..3 / DR-RAG-* (filtered, cited, fallback) | `test_qa_audit_rag_graph::test_rag_*` | **PASS** |
| FR-RSK-1..4 (bounded, threshold, named factors) | `test_qa_properties::test_risk_score_always_bounded`, e2e | **PASS** (but model-nuance bound unenforced → D-05) |
| FR-RC-1..3 (one cause, conservative) | e2e matrix, build `test_perception` | **PASS** |
| FR-PLN-1..5 / §9 (min-cost, never deny defect, no exec) | `test_qa_boundaries::test_selection_*` | **PASS** |
| FR-GRD-1..4 / NFR-SAF-* (caps, clamp, non-bypassable) | `test_qa_financial_integrity::*`, `test_qa_properties::test_guardrail_invariants…`, injection | **FAIL** (D-02 negatives; D-05 model bypass) |
| FR-HIL-1..5 (pause/resume, modify re-checked, restart) | `test_qa_durability_eval::test_resume_*`, `test_reviewer_modify_over_value_is_blocked` | **PARTIAL** (over-cap blocked ✅; negative not ✗ D-02; cross-process restart UNVERIFIED) |
| FR-EXE-1..3 (approved only, idempotent, safe-fail) | `test_qa_concurrency::*`, `test_qa_faults::*` | **FAIL** (D-03 concurrency; D-04 emit-orphan) |
| FR-RES-1..3 / NFR-CMP-* (faithful, no leak, refund-right) | `test_no_resolution_reply_leaks_risk_internals`, `test_genuine_refund_not_defaulted_to_store_credit_only` | **PASS** |
| FR-LOG-1..2 (full record, append-only audit) | `test_qa_audit_rag_graph::*` | **PARTIAL** (insert-only ✅; reconciliation undercount D-06) |
| FR-EVT-1..3 / DR-EVT-1 (idempotent, DLQ) | `test_qa_faults::test_malformed_event…`, build `test_events` | **PASS** (logic); real broker UNVERIFIED |
| FR-RPT-1..2 (metrics; 0 violations) | `test_qa_durability_eval::test_eval_metric_math…` | **PASS** (but INR-saved undercount via D-06) |
| NFR-PERF-1..2 (p95 ≤ 8s; no shared state) | `test_qa_perf`, `test_distinct_concurrent_requests_no_state_bleed` | **PASS (orchestration)**; real-infra p95 UNVERIFIED |
| NFR-REL-1..3 (checkpoint, cap, bounded retries) | `test_qa_audit_rag_graph::test_checkpointer_persists…`, `test_qa_faults::test_clarification_loop…` | **PASS** |
| NFR-SEC-1..4 (secrets, untrusted input, synthetic) | `test_qa_secrets::*`, injection | **PASS** (NFR-SEC-4 reviewer-id SHOULD: not enforced — observation) |
| NFR-OBS-1..3 (trace, structured logs) | build `test_observability` | **PASS** |
| AC-1..AC-6 | aggregate | **AC-2 PASS (eval); AC-5 FAIL (D-03); AC-6 PARTIAL (D-04/D-06); AC-4 PARTIAL (UNVERIFIED cross-process)** |

---

## 7. Coverage gaps / UNVERIFIED (environment-limited, not "pass")

| Item | Why | What WAS done instead |
|---|---|---|
| Real Postgres `psql`/schema/seed smoke (T-SMOKE-1) | Docker daemon unavailable | config-load + fail-fast verified; in-memory mirror of the schema exercised |
| True cross-**process** restart-resume (AC-4 / T-DUR-1) | needs a live Postgres checkpointer | resume verified via a **new graph object sharing the checkpointer** (proxy) |
| Two real Kafka workers / redelivery on the wire (T-CONC-3) | no broker | `handle_message` idempotency + DLQ logic verified offline |
| DB disconnect mid-graph (T-FAIL-DB-1) | no DB | tool/emit fault injection covered the write-failure paths |
| Real-LLM behavioural quality & real-model injection resistance | no API key (stub) | **safety-regardless-of-LLM** tested with adversarial fake LLMs (stronger for safety) |
| Real-infra p95 (T-PERF-1) | stub + in-memory | orchestration p95 ≈ 30ms measured; real model/DB latency not included |

## 8. Flaky / nondeterminism

No flaky tests observed; the full battery is deterministic (seeded RNG, `AS_OF_DATE` clock pin, stub LLM). One **documented latent** nondeterminism: the in-memory idempotency race (D-03) is masked by the CPython GIL — it is reported as a structural defect, not retried to green.

---

*Reproduce everything:* `pytest tests/ -k qa_` (offline; the 9 failures are the defects above). Per category: `pytest -m safety` / `-m concurrency` / `-m failure` / `-m property` / `-m e2e` / `-m perf`.
