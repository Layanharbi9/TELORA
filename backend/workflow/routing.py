"""Conditional routing: decided from already-persisted request state only.

Routing never inspects agent internals or duplicates SharedWorkflowState -- it
re-reads the single source of truth (RequestService/SQLite) after a node has
already persisted its outcome.
"""
from typing import Any, Mapping

from langgraph.graph import END

from backend.schemas.request import RequestStatus, WorkflowStage
from backend.services.request_service import RequestService


def make_route_after_search(request_service: RequestService):
    def route_after_search(state: Mapping[str, Any]) -> str:
        current = request_service.reconstruct_shared_state(state["request_id"])
        if (current.status, current.stage) == (RequestStatus.PROCESSING, WorkflowStage.DESIGNING):
            return "design_node"
        # NEEDS_ADJUSTMENT (search found nothing usable) or ERROR: nothing further to run here.
        return END
    return route_after_search
