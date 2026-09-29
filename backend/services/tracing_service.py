"""LangSmith configuration and small safe tracing primitives for TELORA."""

from __future__ import annotations

import os
import re
import logging
from datetime import date, datetime
from contextlib import contextmanager
from typing import Any, Callable

from langsmith import traceable
from langsmith.run_helpers import tracing_context


DEFAULT_PROJECT = "TELORA"
TRACE_TAGS = ("telora", "workflow")
_warned_missing_key = False
_warned_trace_failure = False


def tracing_enabled() -> bool:
    global _warned_missing_key
    requested = os.getenv("LANGSMITH_TRACING", "false").strip().lower() in {"1", "true", "yes", "on"}
    if requested and not os.getenv("LANGSMITH_API_KEY"):
        if not _warned_missing_key:
            logging.getLogger(__name__).warning(
                "LANGSMITH_TRACING is enabled but LANGSMITH_API_KEY is not set; remote tracing is disabled."
            )
            _warned_missing_key = True
        return False
    return requested


def project_name() -> str:
    return os.getenv("LANGSMITH_PROJECT", DEFAULT_PROJECT)


def environment_name() -> str:
    return os.getenv("TELORA_ENVIRONMENT", os.getenv("APP_ENV", "development"))


def sanitize(value: Any) -> Any:
    """Remove secret-like values while keeping structured business evidence."""
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if normalized in {"customer_name", "username", "email", "phone", "phone_number"}:
                continue
            if normalized in {"chain_of_thought", "reasoning", "policy_rationale", "intent",
                              "intended_tradeoffs", "internal_reasoning", "analysis"}:
                continue
            if any(part in normalized for part in ("api_key", "secret", "password", "authorization", "access_token")):
                safe[str(key)] = "[REDACTED]"
            else:
                safe[str(key)] = sanitize(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    model_dump = getattr(type(value), "model_dump", None)
    if callable(model_dump):
        return sanitize(model_dump(value, mode="json"))
    to_dict = getattr(type(value), "to_dict", None)
    if callable(to_dict):
        return sanitize(to_dict(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str):
            return re.sub(r"\b(?:sk|rk|lsv2)_[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        return value
    return f"[UNSERIALIZABLE:{type(value).__name__}]"


def trace_span(
    name: str,
    action: Callable[[], Any],
    *,
    inputs: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    tags: tuple[str, ...] | list[str] = (),
    run_type: str = "chain",
    run_id_callback: Callable[[str], None] | None = None,
    run_context_callback: Callable[[dict[str, Any]], None] | None = None,
    parent_run: Any = None,
) -> Any:
    """Trace one real action. It is a local no-op when LANGSMITH_TRACING is off."""
    global _warned_trace_failure
    action_state: dict[str, Any] = {"called": False, "result": None, "error": None}

    def invoke(_payload: dict[str, Any], **_trace_context):
        action_state["called"] = True
        if run_id_callback is not None or run_context_callback is not None:
            try:
                from langsmith.run_helpers import get_current_run_tree
                current_run = get_current_run_tree()
                if current_run is not None:
                    if run_id_callback is not None:
                        run_id_callback(str(current_run.id))
                    if run_context_callback is not None:
                        run_context_callback({
                            "run_id": str(current_run.id),
                            "session_id": str(current_run.session_id) if current_run.session_id else None,
                            "trace_id": str(current_run.trace_id) if current_run.trace_id else None,
                            "parent_run": current_run,
                        })
            except Exception:
                # Capturing an observability identifier must never affect TELORA.
                pass
        try:
            action_state["result"] = action()
            return action_state["result"]
        except Exception as error:
            action_state["error"] = error
            raise

    traced = traceable(
        name=name, run_type=run_type, project_name=project_name(),
        enabled=tracing_enabled(), tags=[*TRACE_TAGS, *tags],
        process_inputs=sanitize, process_outputs=sanitize,
    )(invoke)
    try:
        if parent_run is not None:
            with tracing_context(project_name=project_name(), parent=parent_run,
                                 enabled=tracing_enabled(), tags=[*TRACE_TAGS, *tags]):
                return traced(
                    sanitize(inputs or {}),
                    langsmith_extra={"metadata": sanitize({"environment": environment_name(), **(metadata or {})})},
                )
        return traced(sanitize(inputs or {}),
                      langsmith_extra={"metadata": sanitize({"environment": environment_name(), **(metadata or {})})})
    except Exception as error:
        if action_state["error"] is not None:
            raise action_state["error"]
        if not _warned_trace_failure:
            safe_error = str(sanitize(str(error)))[:300]
            logging.getLogger(__name__).warning(
                "LangSmith trace write failed for '%s' (%s: %s); workflow continues.",
                name, type(error).__name__, safe_error)
            _warned_trace_failure = True
        if action_state["called"]:
            return action_state["result"]
        # A tracer setup failure before invoking the wrapped function must not
        # prevent the TELORA operation from running exactly once.
        return action()


def record_event(name: str, *, inputs: dict[str, Any] | None = None,
                 outputs: dict[str, Any] | None = None,
                 metadata: dict[str, Any] | None = None,
                 tags: tuple[str, ...] | list[str] = (),
                 parent_run: Any = None) -> dict[str, Any]:
    """Create a short structured event span for a committed workflow boundary."""
    result = sanitize(outputs or {})
    return trace_span(name, lambda: result, inputs=inputs, metadata=metadata,
                      tags=tags, run_type="chain", parent_run=parent_run)


def record_evaluation(agent: str, metrics: list[dict[str, Any]], *,
                      request_id: str, agent_run_id: str | None,
                      session_id: str | None = None,
                      parent_run: Any = None,
                      inputs: dict[str, Any] | None = None) -> dict[str, Any]:
    """Show scores as an Evaluation child span and attach LangSmith feedback.

    Scores are deterministic checks of structured runtime evidence. The helper
    is best-effort: telemetry failures never affect a customer workflow.
    """
    normalized = []
    for item in metrics:
        score = max(0.0, min(1.0, float(item.get("score", 0.0))))
        normalized.append({"key": item["key"], "score": score,
                           "value": item.get("value", "PASS" if score == 1 else "FAIL"),
                           "comment": str(item.get("comment", ""))[:600]})
    scored_metrics = [row for row in normalized if row["value"] != "NOT_APPLICABLE"]
    aggregate = round(sum(row["score"] for row in scored_metrics) / len(scored_metrics), 4) if scored_metrics else None
    summary = {"agent": agent, "status": "NOT_APPLICABLE" if aggregate is None else
               ("PASS" if aggregate == 1 else "REVIEW"),
               "aggregate_score": aggregate, "metrics": normalized,
               "evaluator_type": "deterministic"}
    result = record_event(
        "evaluation", inputs=inputs or {"request_id": request_id}, outputs=summary,
        metadata={"request_id": request_id, "workflow_id": request_id,
                  "agent": agent, "stage": "EVALUATION", "status": summary["status"]},
        tags=("evaluation", agent), parent_run=parent_run,
    )
    if agent_run_id and tracing_enabled() and scored_metrics:
        try:
            from langsmith import Client
            client = Client()
            # SmithDB feedback writes require the project UUID, called
            # session_id in the SDK. Prefer the run's actual project ID.
            project_id = session_id
            if not project_id:
                project_id = str(client.read_project(project_name=project_name()).id)
            for metric in scored_metrics:
                client.create_feedback(
                    run_id=agent_run_id,
                    session_id=project_id,
                    key=f"{agent}.{metric['key']}",
                    score=metric["score"],
                    comment=metric["comment"],
                    extra={"request_id": request_id, "evaluator_type": "deterministic"},
                )
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Could not attach LangSmith evaluation feedback (%s).", type(error).__name__)
    return result


@contextmanager
def workflow_context(*, metadata: dict[str, Any], tags: tuple[str, ...] | list[str] = ()):
    """Propagate consistent project and request metadata to LangChain/LangGraph children."""
    with tracing_context(
        project_name=project_name(),
        tags=[*TRACE_TAGS, *tags],
        metadata=sanitize({"environment": environment_name(), **metadata}),
        enabled=tracing_enabled(),
    ):
        yield


def traced_action(name: str, action: Callable[[], Any], *, request_id: str,
                  customer_id: str | None = None, workflow_id: str | None = None,
                  metadata: dict[str, Any] | None = None, tags=(),
                  parent_run_id: str | None = None) -> Any:
    """Make a workflow operation a root run or a child of an active context."""
    details = {"workflow_id": workflow_id or request_id, "request_id": request_id,
               "customer_id": customer_id, "agent_system": "TELORA", **(metadata or {})}
    parent = None
    if parent_run_id and tracing_enabled():
        try:
            from langsmith import Client
            # tracing_context(parent=...) expects a RunTree or dotted-order
            # string, not a bare UUID. Resolve our persisted run ID first.
            parent = Client().read_run(parent_run_id).dotted_order
        except Exception as error:
            logging.getLogger(__name__).warning(
                "Could not resolve stored LangSmith parent run (%s); starting a new root trace.",
                type(error).__name__)
    with tracing_context(project_name=project_name(), tags=[*TRACE_TAGS, *tags],
                         metadata=sanitize({"environment": environment_name(), **details}),
                         parent=parent, enabled=tracing_enabled()):
        result = trace_span(name, action, inputs={"request_id": request_id,
                                                   "customer_id": customer_id},
                            metadata=details, tags=tags)
    if parent_run_id and tracing_enabled():
        try:
            from langsmith import Client
            Client().update_run(parent_run_id, outputs=sanitize(result))
        except Exception as error:
            # `logging` is the module import; a local import here would shadow it for the
            # whole function and break the warning above (UnboundLocalError).
            logging.getLogger(__name__).warning("Could not update workflow root output (%s)", type(error).__name__)
    return result
