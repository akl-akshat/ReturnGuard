"""Runtime dependencies injected into graph nodes.

Nodes keep the simple ``node(state) -> dict`` signature and pull their collaborators from
a process-level ``Deps`` (data access, repository, LLM). Offline the default is an
in-memory, stub-LLM stack; the service/worker call :func:`set_deps` at startup to install
the Postgres-backed, real-LLM stack. The Postgres path is stateless per request, so this
does not introduce shared mutable in-process state across workers (NFR-PERF-2).
"""

from __future__ import annotations

from dataclasses import dataclass

from agent.llm import LLMClient, get_llm
from db.repository import InMemoryRepository, Repository
from tools.data_access import DataAccess, LocalDataAccess


@dataclass
class Deps:
    data_access: DataAccess
    repo: Repository
    llm: LLMClient


_deps: Deps | None = None


def get_deps() -> Deps:
    global _deps
    if _deps is None:
        repo = InMemoryRepository()
        _deps = Deps(data_access=LocalDataAccess(repo), repo=repo, llm=get_llm())
    return _deps


def set_deps(deps: Deps) -> None:
    global _deps
    _deps = deps


def reset_deps() -> None:
    global _deps
    _deps = None
