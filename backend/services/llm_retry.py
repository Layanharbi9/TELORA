"""One bounded technical retry per structured LLM operation."""

import json
import logging
from collections.abc import Callable
from typing import TypeVar

from langchain_core.exceptions import OutputParserException
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import ValidationError

from backend.services.tracing_service import trace_span
from backend.services.tracing_service import record_event

T = TypeVar("T")
logger = logging.getLogger(__name__)


class InvalidStructuredOutput(ValueError):
    """A parsed LLM result violates its original-requirement references."""


def retry_category(error: Exception) -> str | None:
    if isinstance(error, (ValidationError, OutputParserException, InvalidStructuredOutput)):
        return "INVALID_SCHEMA"
    if isinstance(error, APITimeoutError):
        return "TIMEOUT"
    if isinstance(error, APIConnectionError):
        return "CONNECTION"
    if isinstance(error, APIStatusError):
        body = error.body if isinstance(error.body, dict) else {}
        nested = body.get("error")
        if isinstance(nested, dict):
            body = nested
        identifiers = {value for value in (body.get("code"), body.get("type"),
                                           getattr(error, "code", None), getattr(error, "type", None))
                       if isinstance(value, str)}
        if identifiers & {"credit_balance_exhausted", "insufficient_quota", "billing_hard_limit_reached"}:
            return None
        if isinstance(error, RateLimitError):
            # Unknown 429s fail closed. Never infer a transient limit from HTTP alone.
            if identifiers & {"rate_limit_exceeded", "rate_limit_error", "requests", "tokens"}:
                return "TEMPORARY_RATE_LIMIT"
            return None
        if error.status_code in {500, 502, 503, 504}:
            return "SERVER_ERROR"
    return None


def run_llm_operation(operation: str, call: Callable[[], T]) -> tuple[T, dict]:
    """Retry the same operation once; retain original exceptions on terminal failure.

    Successful metadata goes into SearchTrace. Failed-run metadata is logged safely
    and attached to the raised exception because incomplete business states aren't saved.
    """
    failures = []
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            result = trace_span(
                f"llm_operation.{operation.lower()}", call,
                inputs={"operation": operation, "attempt": attempt, "max_attempts": max_attempts},
                metadata={"operation": operation, "attempt": attempt,
                          "max_attempts": max_attempts, "agent": _agent_for(operation)},
                tags=("llm", "retry" if attempt > 1 else "first-attempt"),
                run_type="llm",
            )
        except Exception as error:
            category = retry_category(error)
            failures.append({"attempt": attempt, "category": category or "NON_RETRYABLE"})
            retryable = category is not None and attempt < max_attempts
            record_event(
                "llm.retry_decision",
                inputs={"operation": operation, "attempt": attempt, "max_attempts": max_attempts},
                outputs={"error_category": category or "NON_RETRYABLE",
                         "retryable": retryable,
                         "decision": "RETRY" if retryable else "FAIL",
                         "final_result": "PENDING" if retryable else "ERROR"},
                metadata={"operation": operation, "attempt": attempt,
                          "max_attempts": max_attempts, "agent": _agent_for(operation)},
                tags=("llm", "retry", "error"),
            )
            metadata = {"operation": operation, "attempts": attempt,
                        "max_attempts": max_attempts,
                        "retry_count": attempt - 1, "retry_reason": failures[0]["category"] if attempt == 2 else None,
                        "failures": list(failures), "final_result": "ERROR"}
            if attempt == 1 and category is not None:
                continue
            error.llm_retry = metadata
            logger.error("LLM operation failed: %s", json.dumps(metadata))
            raise
        metadata = {"operation": operation, "attempts": attempt, "max_attempts": max_attempts,
                    "retry_count": attempt - 1, "retry_reason": failures[0]["category"] if failures else None,
                    "failures": list(failures), "final_result": "SUCCESS"}
        record_event("llm.operation_result", inputs={"operation": operation,
                     "attempts": attempt, "max_attempts": max_attempts}, outputs=metadata,
                     metadata={"operation": operation, "agent": _agent_for(operation)},
                     tags=("llm", "success"))
        return result, metadata


def _agent_for(operation: str) -> str:
    return "search-agent" if operation in {"PLAN", "ASSESS", "JUDGE", "REASSESS"} else "solution-design"
