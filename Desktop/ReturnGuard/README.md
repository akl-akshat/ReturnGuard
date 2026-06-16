# ReturnGuard

**Autonomous Returns-Deflection & Resolution Agent** — an agentic, constrained-autonomy
system for e-commerce post-order resolution.

ReturnGuard intercepts return / cancellation / refund requests and resolves them by selecting
the **lowest-cost action that satisfies platform policy, protects seller margin, and preserves
customer satisfaction** — escalating to a human only when warranted.

It is built on a **stateful agent graph** (LangGraph) behind a service layer (FastAPI), driven
both synchronously (chat / API) and asynchronously (a Kafka event pipeline), with **deterministic
financial guardrails**, **human-in-the-loop** escalation, an **event-driven pipeline**, and an
**evaluation harness** with hard safety gates.

> Status: **under active construction** — see [`docs/ReturnGuard_Build_Plan.docx`](docs/ReturnGuard_Build_Plan.docx)
> for the phase-by-phase build runbook and [`docs/ReturnGuard_SRS.pdf`](docs/ReturnGuard_SRS.pdf)
> for the full specification (the spec is the source of truth).

## Why this exists

In Indian e-commerce, fashion return rates run 25–35% (≈40% in festive periods); COD
return-to-origin runs 20–40% of COD orders; sellers lose an estimated 8–15% of monthly revenue
to unrecovered return losses. Most handling today is a static form plus a human queue. ReturnGuard
replaces that implicit human decision step with an **auditable, constrained, autonomous agent**.

## Architecture at a glance

```
Customer / API  ──▶  FastAPI Service  ◀──▶  Operator Dashboard
                          │
   Kafka ingest  ───────▶ Agent Orchestrator (LangGraph) ◀──── review queue / resume / metrics
   (returns.requests)      │            │
        reads (MCP)  ──────┘            └────  actions (guardrailed, simulated tools)
              │                                      │
              ▼                                      ▼
        MCP data servers                       Action tools
   (order / customer / fraud)        (refund / exchange / coupon / credit / …)
              └──────────────┬───────────────────────┘
                             ▼
        PostgreSQL (+ pgvector): orders, customers, policies, resolutions,
        audit_log, escalations, eval_cases, vector index, LangGraph checkpointer
                             │
                             ▼
        Kafka emit: resolutions / escalations / audit / outcomes   ·   Tracing: LangSmith / Langfuse
```

## License

[MIT](LICENSE) © 2026 Akshat Lakhera
