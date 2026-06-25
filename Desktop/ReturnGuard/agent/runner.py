"""Convenience helpers to drive the compiled graph (used by service, worker, eval, tests)."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command


def run_config(thread_id: str, recursion_limit: int = 50) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}


def is_paused(graph, config: dict[str, Any]) -> bool:
    """True if the graph is paused at an interrupt (awaiting a human decision)."""
    state = graph.get_state(config)
    return bool(state.next)


def resume(graph, config: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Resume a paused graph with a reviewer decision payload."""
    return graph.invoke(Command(resume=decision), config)
