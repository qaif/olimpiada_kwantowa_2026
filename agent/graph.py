"""
Szkielet pętli agentycznej (LangGraph) dla platformy Olimpiady.

Graf odpowiada sekcji 3.3 w docs/PROJEKT.md. Węzły LLM są tu stubami zwracającymi
częściowe aktualizacje stanu; węzły narzędziowe (lint/build/test/commit) wołają
sandbox przez `tools.sandbox`. Uruchomienie wymaga: langgraph, langchain-anthropic,
pydantic. Sandbox: osobny kontener z własnym demonem Dockera (patrz PROJEKT.md 3.8).
"""
from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

# --------------------------------------------------------------------------- #
# Stan współdzielony
# --------------------------------------------------------------------------- #


class TaskSpec(TypedDict):
    id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    contracts: dict
    files_expected: list[str]
    depends_on: list[str]
    security_notes: list[str]
    status: Literal["todo", "in_progress", "blocked", "done", "escalated"]


class TestReport(TypedDict):
    passed: int
    failed: int
    errors: int
    failures: list[dict]
    coverage_changed_files: float | None


class ReviewReport(TypedDict):
    verdict: Literal["APPROVE", "REQUEST_CHANGES", "BLOCK"]
    findings: list[dict]


class Diagnosis(TypedDict):
    kind: Literal["lint", "build", "test", "review", "e2e"]
    signature: str
    root_cause_hypothesis: str
    fix_plan: list[str]
    files_to_touch: list[str]
    is_repeat: bool
    route_to: Literal["coder", "devops", "qa"]


class AgentState(TypedDict, total=False):
    spec: dict
    backlog: list[TaskSpec]
    current_task: TaskSpec | None
    artifacts: dict[str, str]
    diff_last: str
    build_log: str
    lint_ok: bool
    build_ok: bool
    test_report: TestReport | None
    review_report: ReviewReport | None
    diagnosis: Diagnosis | None
    error_history: Annotated[list[dict], operator.add]
    decisions: Annotated[list[str], operator.add]
    human_notes: Annotated[list[str], operator.add]
    iteration: int
    max_iterations: int
    replan_count: int
    system_done: bool


MAX_ITERATIONS = 4
MAX_REPLANS = 2

# --------------------------------------------------------------------------- #
# Węzły LLM (stuby – w implementacji każdy wywołuje model ze structured output)
# --------------------------------------------------------------------------- #


def architect(state: AgentState) -> AgentState:
    """Produkuje spec (ERD, OpenAPI, ADR) i atomowy backlog. Przy re-planie dzieli task."""
    if state.get("current_task") and state.get("diagnosis"):
        # re-plan: podziel current_task na mniejsze, wróć do backlogu
        task = state["current_task"]
        split = [
            {**task, "id": f"{task['id']}a", "title": f"{task['title']} (część 1)"},
            {**task, "id": f"{task['id']}b", "title": f"{task['title']} (część 2)",
             "depends_on": [f"{task['id']}a"]},
        ]
        return {
            "backlog": split + state["backlog"],
            "current_task": None,
            "replan_count": state.get("replan_count", 0) + 1,
            "iteration": 0,
            "decisions": [f"ADR: task {task['id']} podzielony po {state['iteration']} iteracjach"],
        }
    # pierwsze uruchomienie: spec + backlog z wymagań (LLM)
    return {"spec": {}, "backlog": [], "iteration": 0, "replan_count": 0,
            "max_iterations": MAX_ITERATIONS}


def human_gate_architecture(state: AgentState) -> AgentState:
    decision = interrupt({"type": "approve_architecture", "spec": state["spec"],
                          "backlog": [t["id"] for t in state["backlog"]]})
    return {"human_notes": [str(decision)]}


def pick_next_task(state: AgentState) -> AgentState:
    done_ids = {t["id"] for t in state["backlog"] if t["status"] == "done"}
    for task in state["backlog"]:
        if task["status"] == "todo" and set(task["depends_on"]) <= done_ids:
            task["status"] = "in_progress"
            return {"current_task": task, "iteration": 0, "replan_count": 0,
                    "test_report": None, "review_report": None, "diagnosis": None}
    return {"current_task": None}


def coder(state: AgentState) -> AgentState:
    """Generuje/poprawia kod. Dostaje current_task, spec, diagnosis, error_history."""
    return {"diff_last": "", "iteration": state.get("iteration", 0) + 1}


def devops(state: AgentState) -> AgentState:
    """Dockerfile, compose, env, entrypoint. Reaguje na nowe zależności/usługi w diffie."""
    return {}


def qa(state: AgentState) -> AgentState:
    """Testy jednostkowe/integracyjne dla acceptance_criteria + E2E."""
    return {}


def diagnose(state: AgentState) -> AgentState:
    """Zamienia log/raport w hipotezę przyczyny. Wykrywa powtórki sygnatur."""
    signature = "<fingerprint>"
    seen = {e["signature"] for e in state.get("error_history", [])}
    diagnosis: Diagnosis = {
        "kind": "test", "signature": signature, "root_cause_hypothesis": "",
        "fix_plan": [], "files_to_touch": [], "is_repeat": signature in seen,
        "route_to": "coder",
    }
    return {
        "diagnosis": diagnosis,
        "error_history": [{"iteration": state.get("iteration", 0), **diagnosis}],
    }


def critic(state: AgentState) -> AgentState:
    """Review bezpieczeństwa (SQLi, upload, RBAC) i zgodności ze spec (OpenAPI diff)."""
    return {"review_report": {"verdict": "APPROVE", "findings": []}}


def human_gate_escalation(state: AgentState) -> AgentState:
    decision = interrupt({"type": "escalation", "task": state["current_task"],
                          "error_history": state.get("error_history", []),
                          "review_report": state.get("review_report")})
    task = state["current_task"]
    task["status"] = "escalated"
    return {"human_notes": [str(decision)], "current_task": None}


# --------------------------------------------------------------------------- #
# Węzły narzędziowe (wołają sandbox; tu tylko kontrakt)
# --------------------------------------------------------------------------- #


def lint(state: AgentState) -> AgentState:
    # sandbox.run("ruff check . && mypy . && python manage.py check && python manage.py makemigrations --check")
    return {"lint_ok": True}


def build(state: AgentState) -> AgentState:
    # sandbox.compose_up_build(timeout=600); poll `docker compose ps --format json` aż healthy
    return {"build_ok": True, "build_log": ""}


def test(state: AgentState) -> AgentState:
    # sandbox.run("pytest -x --junitxml=/tmp/junit.xml --cov --cov-report=json") -> TestReport
    return {"test_report": {"passed": 0, "failed": 0, "errors": 0, "failures": [],
                            "coverage_changed_files": None}}


def e2e(state: AgentState) -> AgentState:
    # czysty `compose down -v && up --build`, seed_demo, playwright
    return {"test_report": {"passed": 0, "failed": 0, "errors": 0, "failures": [],
                            "coverage_changed_files": None}}


def commit(state: AgentState) -> AgentState:
    # gitleaks protect; git commit -m "feat(...): ... (T-xx)"; git tag task/T-xx
    task = state["current_task"]
    task["status"] = "done"
    return {"current_task": None}


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def after_pick(state: AgentState) -> str:
    return "e2e" if state.get("current_task") is None else "coder"


def after_lint(state: AgentState) -> str:
    return "build" if state["lint_ok"] else "diagnose"


def after_build(state: AgentState) -> str:
    return "qa" if state["build_ok"] else "diagnose"


def after_test(state: AgentState) -> str:
    r = state["test_report"]
    return "critic" if r and r["failed"] == 0 and r["errors"] == 0 else "diagnose"


def after_critic(state: AgentState) -> str:
    verdict = state["review_report"]["verdict"]
    if verdict == "BLOCK":
        return "human_gate_escalation"
    if verdict == "REQUEST_CHANGES":
        return "diagnose"
    return "commit" if definition_of_done(state) else "diagnose"


def definition_of_done(state: AgentState) -> bool:
    r = state.get("test_report") or {}
    cov = r.get("coverage_changed_files")
    return bool(
        state.get("lint_ok") and state.get("build_ok")
        and r.get("failed", 1) == 0 and r.get("errors", 1) == 0
        and (cov is None or cov >= 0.85)
        and state["review_report"]["verdict"] == "APPROVE"
        and not any(f["severity"] == "high" for f in state["review_report"]["findings"])
    )


def after_diagnose(state: AgentState) -> str:
    d = state["diagnosis"]
    it = state.get("iteration", 0)
    if it < state.get("max_iterations", MAX_ITERATIONS) and not (d["is_repeat"] and it >= 2):
        return {"coder": "coder", "devops": "devops", "qa": "qa"}[d["route_to"]]
    if state.get("replan_count", 0) < MAX_REPLANS:
        return "architect"          # re-plan: podziel task
    return "human_gate_escalation"


def after_e2e(state: AgentState) -> str:
    r = state["test_report"]
    return END if r["failed"] == 0 and r["errors"] == 0 else "diagnose"


# --------------------------------------------------------------------------- #
# Budowa grafu
# --------------------------------------------------------------------------- #


def build_graph():
    g = StateGraph(AgentState)
    for name, fn in [
        ("architect", architect), ("human_gate_architecture", human_gate_architecture),
        ("pick_next_task", pick_next_task), ("coder", coder), ("devops", devops),
        ("lint", lint), ("build", build), ("qa", qa), ("test", test),
        ("critic", critic), ("diagnose", diagnose), ("commit", commit), ("e2e", e2e),
        ("human_gate_escalation", human_gate_escalation),
    ]:
        g.add_node(name, fn)

    g.add_edge(START, "architect")
    g.add_conditional_edges("architect", lambda s: "pick_next_task" if s.get("replan_count", 0) else "human_gate_architecture")
    g.add_edge("human_gate_architecture", "pick_next_task")
    g.add_conditional_edges("pick_next_task", after_pick)
    g.add_edge("coder", "devops")
    g.add_edge("devops", "lint")
    g.add_conditional_edges("lint", after_lint)
    g.add_conditional_edges("build", after_build)
    g.add_edge("qa", "test")
    g.add_conditional_edges("test", after_test)
    g.add_conditional_edges("critic", after_critic)
    g.add_conditional_edges("diagnose", after_diagnose)
    g.add_edge("commit", "pick_next_task")
    g.add_edge("human_gate_escalation", "pick_next_task")
    g.add_conditional_edges("e2e", after_e2e)

    checkpointer = SqliteSaver.from_conn_string("runs/checkpoints.sqlite")
    return g.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    app = build_graph()
    config = {"configurable": {"thread_id": "olimpiada-run-1"}}
    for event in app.stream({"backlog": [], "error_history": [], "decisions": [], "human_notes": []},
                            config=config, stream_mode="updates"):
        print(event)
