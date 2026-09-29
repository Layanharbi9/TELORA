"""Read-only system monitoring for the admin console, sourced from LangSmith.

TELORA already traces every workflow, agent, LLM call and runtime evaluation to
LangSmith (see tracing_service). This module only reads those runs back and
aggregates them: latency, tokens, cost, errors and evaluation scores. It never
writes to LangSmith and never affects a customer workflow.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Any

from backend.services.tracing_service import project_name, sanitize

logger = logging.getLogger(__name__)

CACHE_SECONDS = 30
AGENTS = {
    "search_agent": "Search Agent",
    "solution_design_agent": "Solution Design Agent",
    "apply_tool": "Apply Tool",
}
OPERATION_LABELS = {
    "PLAN": "Plan search", "ASSESS": "Assess candidates", "JUDGE": "Judge coverage",
    "REASSESS": "Re-assess", "SOLUTION_DESIGN_PLAN": "Plan strategies",
    "SOLUTION_BASIC_DESIGN": "Design solution", "SOLUTION_REVIEW": "Review solution",
    "SOLUTION_COMPARISON": "Compare solutions", "SOLUTION_RECOMMENDATION": "Recommend",
}
_cache: dict[int, tuple[float, dict]] = {}
_lock = threading.Lock()


def _latency(run) -> float | None:
    if run.start_time and run.end_time:
        return round((run.end_time - run.start_time).total_seconds(), 3)
    return None


def _p95(values: list[float]) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]


def _avg(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(mean(values), 4) if values else None


def _sum(values) -> float:
    return round(sum(float(v) for v in values if v is not None), 6)


def _metadata(run) -> dict:
    return ((run.extra or {}).get("metadata") or {}) if run.extra else {}


def _error_text(run) -> str | None:
    if not run.error:
        return None
    lines = [line.strip() for line in str(sanitize(run.error)).strip().splitlines() if line.strip()]
    # LangGraph appends "During task with name ..." after the real exception line; prefer the exception.
    exception = [line for line in lines if re.match(r"^[\w.]*(Error|Exception)\b.*:", line)]
    text = (exception[-1] if exception else lines[-1]) if lines else ""
    return text[:300] or "Error"


def _unconfigured(reason: str) -> dict:
    return {"configured": False, "reason": reason, "project": project_name()}


def system_overview(days: int = 7, *, refresh: bool = False) -> dict:
    days = max(1, min(int(days), 90))
    now = time.monotonic()
    with _lock:
        cached = _cache.get(days)
        if cached and not refresh and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    result = _build(days)
    if result.get("configured"):
        with _lock:
            _cache[days] = (now, result)
    return result


def _build(days: int) -> dict:
    if not os.getenv("LANGSMITH_API_KEY", "").strip():
        return _unconfigured("LANGSMITH_API_KEY is not set on the server, so there is no trace data to read. "
                             "Add it to .env and restart the server.")
    try:
        from langsmith import Client
        client = Client()
        project = project_name()
        since = datetime.now(timezone.utc) - timedelta(days=days)
        agent_filter = "in(name, [" + ", ".join(f'"{name}"' for name in AGENTS) + "])"
        with ThreadPoolExecutor(max_workers=3) as pool:
            roots_f = pool.submit(lambda: list(client.list_runs(
                project_name=project, is_root=True, filter='eq(name, "telora.workflow")', start_time=since)))
            agents_f = pool.submit(lambda: list(client.list_runs(
                project_name=project, filter=agent_filter, start_time=since)))
            llm_f = pool.submit(lambda: list(client.list_runs(
                project_name=project, run_type="llm", filter='eq(name, "ChatOpenAI")', start_time=since)))
            roots, agent_runs, llm_runs = roots_f.result(), agents_f.result(), llm_f.result()
        # Count only runs inside a customer workflow trace. Agent runs traced by tests,
        # evaluation experiments or scripts share the project but are not customer requests.
        workflow_traces = {run.trace_id for run in roots}
        all_agent_runs = len(agent_runs)
        agent_runs = [run for run in agent_runs if run.trace_id in workflow_traces]
        llm_runs = [run for run in llm_runs if run.trace_id in workflow_traces]
        excluded_runs = all_agent_runs - len(agent_runs)
        feedback = list(client.list_feedback(run_ids=[run.id for run in agent_runs])) if agent_runs else []
    except Exception as error:
        logger.warning("Could not read LangSmith monitoring data (%s).", type(error).__name__)
        name = type(error).__name__
        if "NotFound" in name:
            return _unconfigured(f"LangSmith project '{project_name()}' was not found. Run one request with tracing on first.")
        if "Auth" in name or "Forbidden" in name:
            return _unconfigured("LangSmith rejected the API key. Check LANGSMITH_API_KEY and restart the server.")
        return _unconfigured(f"Could not reach LangSmith ({name}). Check the connection and try again.")
    result = _aggregate(days, roots, agent_runs, llm_runs, feedback)
    result["excluded_agent_runs"] = excluded_runs
    return result


def _aggregate(days: int, roots, agent_runs, llm_runs, feedback) -> dict:
    host = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").replace("api.smith", "smith").rstrip("/")
    url = lambda run: f"{host}{run.app_path}" if getattr(run, "app_path", None) else None

    # evaluation feedback belongs to agent runs; map it back to agents and workflow traces
    agent_by_id = {run.id: run for run in agent_runs}
    scores_by_trace: dict[Any, list[float]] = defaultdict(list)
    scores_by_agent: dict[str, list[float]] = defaultdict(list)
    metrics: dict[str, dict] = {}
    for item in feedback:
        if item.score is None:
            continue
        run = agent_by_id.get(item.run_id)
        agent_name = run.name if run else "unknown"
        score = float(item.score)
        scores_by_agent[agent_name].append(score)
        if run is not None:
            scores_by_trace[run.trace_id].append(score)
        agent_key, _, metric = item.key.partition(".")
        entry = metrics.setdefault(item.key, {"key": item.key, "agent": agent_key, "metric": metric or item.key,
                                              "scores": [], "failures": [], "last_at": None})
        entry["scores"].append(score)
        if score < 1:
            entry["failures"].append({"comment": (item.comment or "")[:300], "at": item.created_at})
        created = item.created_at.isoformat() if item.created_at else None
        entry["last_at"] = max(filter(None, [entry["last_at"], created]), default=None)

    evaluations = []
    for entry in metrics.values():
        failures = sorted(entry["failures"], key=lambda f: f["at"] or datetime.min.replace(tzinfo=timezone.utc))
        evaluations.append({
            "agent": entry["agent"], "metric": entry["metric"], "checks": len(entry["scores"]),
            "avg_score": _avg(entry["scores"]), "failed": len(failures),
            "last_failure": failures[-1]["comment"] if failures else None, "last_at": entry["last_at"],
        })
    evaluations.sort(key=lambda e: (e["avg_score"] if e["avg_score"] is not None else 2, e["agent"], e["metric"]))

    agents_by_trace: dict[Any, dict[str, Any]] = defaultdict(dict)
    for run in agent_runs:
        agents_by_trace[run.trace_id][run.name] = run

    agents = []
    for name, label in AGENTS.items():
        runs = [run for run in agent_runs if run.name == name]
        latencies = [_latency(run) for run in runs]
        errors = [run for run in runs if run.status == "error"]
        failed_checks = sum(1 for item in feedback if item.score is not None and item.score < 1
                            and agent_by_id.get(item.run_id) is not None and agent_by_id[item.run_id].name == name)
        agents.append({
            "key": name, "label": label, "runs": len(runs), "errors": len(errors),
            "success_rate": round((len(runs) - len(errors)) / len(runs), 4) if runs else None,
            "avg_latency_s": _avg(latencies), "p95_latency_s": _p95(latencies),
            "avg_tokens": _avg([run.total_tokens for run in runs if run.total_tokens]),
            "avg_cost": _avg([float(run.total_cost) for run in runs if run.total_cost is not None]),
            "total_cost": _sum(run.total_cost for run in runs),
            "eval_score": _avg(scores_by_agent.get(name, [])), "eval_checks": len(scores_by_agent.get(name, [])),
            "failed_checks": failed_checks,
            "last_error": _error_text(max(errors, key=lambda r: r.start_time)) if errors else None,
        })

    operations: dict[tuple, dict] = {}
    models: dict[str, dict] = {}
    for run in llm_runs:
        meta = _metadata(run)
        model = meta.get("ls_model_name") or ((run.extra or {}).get("invocation_params") or {}).get("model") or "unknown"
        operation = meta.get("operation") or "OTHER"
        key = (operation, model)
        op = operations.setdefault(key, {"operation": operation, "label": OPERATION_LABELS.get(operation, operation.title()),
                                         "agent": meta.get("agent"), "model": model, "calls": 0, "errors": 0,
                                         "latencies": [], "tokens": [], "cost": 0.0})
        op["calls"] += 1
        op["errors"] += run.status == "error"
        op["latencies"].append(_latency(run))
        op["tokens"].append(run.total_tokens or 0)
        op["cost"] += float(run.total_cost or 0)
        m = models.setdefault(model, {"model": model, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                      "total_tokens": 0, "cost": 0.0, "latencies": []})
        m["calls"] += 1
        m["prompt_tokens"] += run.prompt_tokens or 0
        m["completion_tokens"] += run.completion_tokens or 0
        m["total_tokens"] += run.total_tokens or 0
        m["cost"] += float(run.total_cost or 0)
        m["latencies"].append(_latency(run))
    order = list(OPERATION_LABELS)
    operations_out = sorted(({
        "operation": op["operation"], "label": op["label"], "agent": op["agent"], "model": op["model"],
        "calls": op["calls"], "errors": op["errors"], "avg_latency_s": _avg(op["latencies"]),
        "avg_tokens": _avg(op["tokens"]), "total_cost": round(op["cost"], 6),
    } for op in operations.values()), key=lambda o: (order.index(o["operation"]) if o["operation"] in order else 99, o["model"]))
    models_out = sorted(({**{k: v for k, v in m.items() if k != "latencies"}, "cost": round(m["cost"], 6),
                          "avg_latency_s": _avg(m["latencies"])} for m in models.values()),
                        key=lambda m: -m["cost"])

    runs_out, daily = [], defaultdict(lambda: {"runs": 0, "errors": 0, "cost": 0.0, "tokens": 0, "latencies": []})
    for run in sorted(roots, key=lambda r: r.start_time, reverse=True):
        meta = _metadata(run)
        scores = scores_by_trace.get(run.trace_id, [])
        stages = agents_by_trace.get(run.trace_id, {})
        # The Apply run joins this trace when the customer applies, so wall time includes customer
        # think-time. Processing time is the agents' own time; fall back to wall time without stages.
        stage_times = [_latency(stage) for stage in stages.values() if _latency(stage) is not None]
        wall = _latency(run)
        latency = round(sum(stage_times), 3) if stage_times else wall
        runs_out.append({
            "run_id": str(run.id), "request_id": meta.get("request_id"), "customer_id": meta.get("customer_id"),
            "status": run.status, "started_at": run.start_time.replace(tzinfo=timezone.utc).isoformat() if run.start_time.tzinfo is None else run.start_time.isoformat(),
            "latency_s": latency, "wall_s": wall, "total_tokens": run.total_tokens or 0,
            "prompt_tokens": run.prompt_tokens or 0, "completion_tokens": run.completion_tokens or 0,
            "cost": float(run.total_cost) if run.total_cost is not None else None,
            "eval_score": _avg(scores), "eval_failed": sum(1 for s in scores if s < 1),
            "stages": {name: _latency(stage) for name, stage in stages.items()},
            "error": _error_text(run), "url": url(run),
        })
        day = daily[run.start_time.date().isoformat()]
        day["runs"] += 1
        day["errors"] += run.status == "error"
        day["cost"] += float(run.total_cost or 0)
        day["tokens"] += run.total_tokens or 0
        day["latencies"].append(latency)

    today = datetime.now(timezone.utc).date()
    daily_out = []
    for offset in range(days - 1, -1, -1):
        key = (today - timedelta(days=offset)).isoformat()
        d = daily.get(key, {"runs": 0, "errors": 0, "cost": 0.0, "tokens": 0, "latencies": []})
        daily_out.append({"date": key, "runs": d["runs"], "errors": d["errors"], "cost": round(d["cost"], 6),
                          "tokens": d["tokens"], "avg_latency_s": _avg(d["latencies"])})

    latencies = [r["latency_s"] for r in runs_out]
    errors = sum(1 for r in runs_out if r["status"] == "error")
    all_scores = [score for scores in scores_by_agent.values() for score in scores]
    total_cost = _sum(r["cost"] for r in runs_out)
    total_tokens = sum(r["total_tokens"] for r in runs_out)
    return {
        "configured": True, "project": project_name(), "window_days": days,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project_url": f"{host}{roots[0].app_path.split('/r/')[0]}" if roots and getattr(roots[0], "app_path", None) else host,
        "kpis": {
            "runs": len(runs_out), "errors": errors,
            "success_rate": round((len(runs_out) - errors) / len(runs_out), 4) if runs_out else None,
            "avg_latency_s": _avg(latencies), "p95_latency_s": _p95(latencies),
            "total_cost": total_cost, "avg_cost": round(total_cost / len(runs_out), 6) if runs_out else None,
            "total_tokens": total_tokens, "avg_tokens": round(total_tokens / len(runs_out)) if runs_out else None,
            "prompt_tokens": sum(r["prompt_tokens"] for r in runs_out),
            "completion_tokens": sum(r["completion_tokens"] for r in runs_out),
            "llm_calls": len(llm_runs), "eval_score": _avg(all_scores), "eval_checks": len(all_scores),
            "eval_failed": sum(1 for s in all_scores if s < 1),
        },
        "agents": agents, "operations": operations_out, "models": models_out,
        "evaluations": evaluations, "daily": daily_out, "runs": runs_out[:60],
    }
