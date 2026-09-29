"""Deterministic request lifecycle coordination; no agent execution or reasoning."""

import json
from contextlib import closing
from uuid import uuid4

from backend.database.db import SearchDatabase
from backend.database.workflow_persistence import (
    BUILD_STAGES, InvalidLifecycleTransition, OUTPUT_FIELDS, recommendation_ids_json,
)
from backend.services.current_plan_service import CurrentPlanService
from backend.schemas.checkpoint import TemporaryWorkflowOutput, WorkflowCheckpoint
from backend.schemas.request import Request, RequestStatus as Status, WorkflowStage as Stage, utc_now
from backend.schemas.requirements import Requirements
from backend.schemas.search import CandidatePool
from backend.schemas.state import SharedWorkflowState
from backend.services.tracing_service import record_event, trace_span
from langsmith.run_helpers import get_current_run_tree


class StateReconstructionError(ValueError):
    """Persisted workflow records are missing a checkpoint or are inconsistent."""


# Source and destination pairs are explicit: status alone never authorizes an event.
TRANSITIONS = {
    "search_succeeded": ((Status.PROCESSING, Stage.SEARCHING), (Status.PROCESSING, Stage.DESIGNING)),
    "search_needs_adjustment": ((Status.PROCESSING, Stage.SEARCHING), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS)),
    "design_completed": ((Status.PROCESSING, Stage.DESIGNING), (Status.PROCESSING, Stage.VALIDATING)),
    "validation_completed": ((Status.PROCESSING, Stage.VALIDATING), (Status.PROCESSING, Stage.PREPARING_SOLUTIONS)),
    "solutions_ready": ((Status.PROCESSING, Stage.PREPARING_SOLUTIONS), (Status.WAITING_FOR_USER, Stage.SELECTION)),
    "solution_design_ready": ((Status.PROCESSING, Stage.DESIGNING), (Status.WAITING_FOR_USER, Stage.SELECTION)),
    "solution_design_needs_adjustment": ((Status.PROCESSING, Stage.DESIGNING), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS)),
    "select_solution": ((Status.WAITING_FOR_USER, Stage.SELECTION), (Status.PROCESSING, Stage.APPLYING)),
    "apply_completed": ((Status.PROCESSING, Stage.APPLYING), (Status.COMPLETED, Stage.COMPLETED)),
    "resubmit_requirements": ((Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS), (Status.PROCESSING, Stage.SEARCHING)),
    "cancel_request": ((Status.WAITING_FOR_USER, Stage.SELECTION), (Status.CANCELLED, Stage.CANCELLED)),
}
ACTIVE_STATES = frozenset(source for source, _ in TRANSITIONS.values())
# Search/Design is running in some executor process (APPLYING excluded).
BUILD_STATES = frozenset((Status.PROCESSING, stage) for stage in BUILD_STAGES)
INTERRUPTED_MESSAGE = "TELORA was interrupted while building your solution. Please try again."


class RequestService:
    """Receive stage outputs and persist their lifecycle events.

    Missing records raise KeyError; invalid/repeated events raise
    InvalidLifecycleTransition. Request creation validates customer identity in
    the SQLite persistence boundary before storing the catalog-backed name.
    Event methods do not infer decisions from candidate or solution contents.
    """

    def __init__(self, database: SearchDatabase | None = None) -> None:
        self.database = database if database is not None else SearchDatabase()

    def create_request(self, request_id: str, customer_id: str, requirements: Requirements,
                       executor_id: str | None = None) -> Request:
        """Persist a backend-originated request ID after catalog identity validation.

        executor_id names the process that will run this request's Search/Design
        work (see backend.services.executor); NULL means unowned/legacy.
        """
        timestamp = utc_now()
        request = Request(request_id=request_id, customer_id=customer_id, requirements=requirements,
                          status=Status.PROCESSING, stage=Stage.SEARCHING,
                          created_at=timestamp, updated_at=timestamp)
        trace_span(
            "request.create", lambda: self.database.create_request_with_checkpoint(request, executor_id),
            inputs={"request_id": request_id, "customer_id": customer_id,
                    "requirements": requirements.to_dict(),
                    "budget": requirements.preferred_budget_sar},
            metadata={"request_id": request_id, "customer_id": customer_id,
                      "workflow_id": request_id, "entity": "requests", "status": "STARTED"},
            tags=("database", "request"),
        )
        root_run = get_current_run_tree()
        if root_run is not None:
            self.database.record_workflow_trace_root(request_id, str(root_run.id), request_id)
        record_event("request.created", inputs={"request_id": request_id, "customer_id": customer_id,
                     "requirements": requirements.to_dict()},
                     outputs={"status": "STARTED", "stage": "SEARCHING",
                              "created_at": timestamp.isoformat()},
                     metadata={"request_id": request_id, "customer_id": customer_id,
                               "workflow_id": request_id, "entity": "requests"},
                     tags=("database", "request"))
        return request

    def create_customer_request(self, customer_id: str, requirements: Requirements,
                                request_id: str | None = None, executor_id: str | None = None) -> Request:
        """Public customer entry; resolve identity and generate the request ID."""
        with closing(self.database._connect()) as connection:
            if connection.execute("SELECT 1 FROM customers WHERE customer_id=?", (customer_id,)).fetchone() is None:
                raise ValueError(f"Unknown customer: {customer_id}")
        from backend.services.current_plan_service import CurrentPlanService
        CurrentPlanService(self.database).initialize_missing_plans()
        return self.create_request(request_id or f"REQ-{uuid4().hex.upper()}", customer_id, requirements,
                                   executor_id=executor_id)

    def start_customer_request(self, customer_id: str, requirements: Requirements,
                               request_id: str | None = None, executor_id: str | None = None) -> Request:
        """Create a request with a backend-generated ID for customer entry points."""
        return self.create_customer_request(customer_id, requirements, request_id=request_id,
                                            executor_id=executor_id)

    def get_request(self, request_id: str) -> Request | None:
        return self.database.get_request(request_id)

    def get_checkpoint(self, request_id: str) -> WorkflowCheckpoint | None:
        return self.database.get_checkpoint(request_id)

    def reconstruct_shared_state(self, request_id: str) -> SharedWorkflowState:
        """Build or restore working memory from the same read-only snapshot path.

        Missing requests raise KeyError. Missing/mismatched checkpoints and invalid
        persisted JSON/schema values raise StateReconstructionError; no repairs or
        writes occur. Database operational errors propagate to the caller.
        """
        try:
            request, checkpoint = self.database.get_workflow_records(request_id)
            if request is None:
                raise KeyError(f"Missing request: {request_id}")
            if checkpoint is None:
                raise StateReconstructionError(f"Missing checkpoint for {request_id}")
            if request.request_id != request_id or checkpoint.request_id != request_id:
                raise StateReconstructionError(f"Mismatched workflow record IDs for {request_id}")
            return SharedWorkflowState.model_validate({
                "request_id": request.request_id,
                "customer_id": request.customer_id,
                "status": request.status,
                "stage": request.stage,
                "requirements": request.requirements.to_dict(),
                **checkpoint.model_dump(mode="python", exclude={"request_id"}),
            })
        except StateReconstructionError:
            raise
        except ValueError as error:
            raise StateReconstructionError(f"Invalid persisted workflow data for {request_id}") from error

    def _complete(self, event: str, request_id: str, *, request_updates: dict | None = None, **outputs) -> None:
        if any(value is None for value in outputs.values()):
            raise ValueError("Stage completion requires non-null outputs")
        source, destination = TRANSITIONS[event]
        self.database.transition_workflow(request_id, allowed_from=frozenset({source}),
                                          status=destination[0], stage=destination[1], outputs=outputs,
                                          request_updates=request_updates)

    def search_succeeded(self, request_id: str, candidate_pool: CandidatePool) -> None:
        self._complete("search_succeeded", request_id, candidate_pool=candidate_pool)

    def search_needs_adjustment(self, request_id: str, candidate_pool: CandidatePool) -> None:
        """Persist the completed search output while requesting new requirements."""
        self._complete("search_needs_adjustment", request_id, candidate_pool=candidate_pool)

    def design_completed(self, request_id: str, candidate_solutions: TemporaryWorkflowOutput) -> None:
        self._complete("design_completed", request_id, candidate_solutions=candidate_solutions)

    def validation_completed(self, request_id: str, solution_validation: TemporaryWorkflowOutput,
                             final_solutions: TemporaryWorkflowOutput) -> None:
        self._complete("validation_completed", request_id,
                       solution_validation=solution_validation, final_solutions=final_solutions)

    def solutions_ready(self, request_id: str) -> None:
        self._complete("solutions_ready", request_id)

    def solution_design_ready(self, request_id: str, candidate_solutions: TemporaryWorkflowOutput,
                              solution_validation: TemporaryWorkflowOutput,
                              final_solutions: TemporaryWorkflowOutput) -> None:
        """Persist Solution Design's already-completed pipeline output in one transition.

        DESIGNING already ran PLAN/DESIGN/QUALITY CHECK/CONSOLIDATE/GUARDRAILS/
        COMPARE/RECOMMENDATION/FINAL OUTPUT internally; no separate VALIDATING or
        PREPARING_SOLUTIONS checkpoint is persisted for this flow. The presented
        solution IDs and SOLUTIONS_PRESENTED are written in the same transaction
        as WAITING_FOR_USER/SELECTION, so no interruption can split them.
        """
        ids_json = recommendation_ids_json(final_solutions)
        updates = {"recommended_solution_ids_json": ids_json}
        if json.loads(ids_json):
            updates["business_status"] = "SOLUTIONS_PRESENTED"
        self._complete("solution_design_ready", request_id, request_updates=updates,
                       candidate_solutions=candidate_solutions,
                       solution_validation=solution_validation, final_solutions=final_solutions)

    def solution_design_needs_adjustment(self, request_id: str, candidate_solutions: TemporaryWorkflowOutput,
                                         solution_validation: TemporaryWorkflowOutput,
                                         final_solutions: TemporaryWorkflowOutput) -> None:
        """Persist the completed attempt when no solution passed Guardrails.

        Temporary workflow policy: routes back to REQUIREMENTS like Search's own
        NO_RESULTS/INSUFFICIENT handling. Return/retry/recovery paths are not
        reconsidered here.
        """
        self._complete("solution_design_needs_adjustment", request_id, candidate_solutions=candidate_solutions,
                       solution_validation=solution_validation, final_solutions=final_solutions)
        self.database.record_recommendations(request_id, final_solutions)

    def select_solution(self, request_id: str, selected_solution: TemporaryWorkflowOutput) -> None:
        payload = selected_solution.model_dump(mode="json") if hasattr(selected_solution, "model_dump") else selected_solution
        selected_id = payload.get("solution_id", payload.get("id")) if isinstance(payload, dict) else None
        if not selected_id:
            raise ValueError("Selected solution must include a solution_id")
        request = self.get_request(request_id)
        checkpoint = self.get_checkpoint(request_id)
        if request is None or checkpoint is None:
            raise KeyError(request_id)
        expected_source = TRANSITIONS["select_solution"][0]
        if (request.status, request.stage) != expected_source:
            raise InvalidLifecycleTransition(
                f"Cannot select a solution for {request_id} from "
                f"{request.status.value}/{request.stage.value}"
            )
        final_solutions = checkpoint.final_solutions
        candidates = final_solutions.get("solutions", []) if isinstance(final_solutions, dict) else []
        canonical = next((item for item in candidates if item.get("solution_id") == selected_id), None)
        if canonical is None:
            raise ValueError(f"Unknown selected solution: {selected_id}")

        package_ids = [canonical["package"]["package_id"]]
        addon_ids = [item["addon_id"] for item in canonical.get("addons", [])]
        with closing(self.database._connect()) as connection:
            customer = connection.execute("SELECT name FROM customers WHERE customer_id=?",
                                          (request.customer_id,)).fetchone()
            if customer is None:
                raise ValueError(f"Unknown customer: {request.customer_id}")
            package, addons, total_price = CurrentPlanService.catalog_composition(
                connection, package_ids, addon_ids)
        target_plan = CurrentPlanService.selected_plan_snapshot(
            request.customer_id, customer[0], package, addons, total_price)
        updates = {
            "business_status": "CUSTOMER_SELECTED",
            "selected_solution_id": selected_id,
            "new_package_ids_json": json.dumps(target_plan["package_ids"], ensure_ascii=False),
            "new_package_names_json": json.dumps(target_plan["package_names"], ensure_ascii=False),
            "new_addon_ids_json": json.dumps(target_plan["addon_ids"], ensure_ascii=False),
            "new_addon_names_json": json.dumps(target_plan["addon_names"], ensure_ascii=False),
            "total_price": total_price,
            "new_plan_json": json.dumps(target_plan, ensure_ascii=False, allow_nan=False),
        }
        self._complete("select_solution", request_id, request_updates=updates, selected_solution=canonical)
        record_event("customer.selection", inputs={"request_id": request_id,
                     "customer_id": request.customer_id,
                     "presented_solution_ids": [item.get("solution_id") for item in candidates],
                     "selected_solution_id": selected_id},
                     outputs={"valid": True, "package_ids": package_ids,
                              "addon_ids": addon_ids, "total_price": total_price},
                     metadata={"request_id": request_id, "customer_id": request.customer_id,
                               "workflow_id": request_id, "stage": "CUSTOMER_SELECTION"},
                     tags=("workflow", "selection"))

    def apply_completed(self, request_id: str, applied_solution: TemporaryWorkflowOutput) -> None:
        self._complete("apply_completed", request_id, applied_solution=applied_solution)

    def resubmit_requirements(self, request_id: str, requirements: Requirements) -> None:
        if not isinstance(requirements, Requirements):
            raise TypeError("requirements must be Requirements")
        source, destination = TRANSITIONS["resubmit_requirements"]
        self.database.transition_workflow(
            request_id, allowed_from=frozenset({source}), status=destination[0], stage=destination[1],
            requirements=requirements, outputs={name: None for name in OUTPUT_FIELDS})

    def cancel_request(self, request_id: str) -> bool:
        """Customer rejects every generated solution; nothing is applied.

        Only WAITING_FOR_USER/SELECTION may be cancelled. Writes only the
        request row (never current_plans or orders) and keeps every checkpoint
        output. Returns False when the request was already CANCELLED
        (idempotent, including a concurrent duplicate that lost the write lock).
        Missing requests raise KeyError; other sources raise
        InvalidLifecycleTransition.
        """
        source, destination = TRANSITIONS["cancel_request"]
        request = self.get_request(request_id)
        if request is None:
            raise KeyError(request_id)
        if (request.status, request.stage) == destination:
            return False
        try:
            self.database.transition_workflow(
                request_id, allowed_from=frozenset({source}), status=destination[0], stage=destination[1],
                request_updates={"business_status": "CANCELLED", "completed_at": utc_now().isoformat()})
        except InvalidLifecycleTransition:
            current = self.get_request(request_id)
            if current is not None and (current.status, current.stage) == destination:
                return False
            raise
        record_event("request.cancelled", inputs={"request_id": request_id},
                     outputs={"status": "CANCELLED", "current_plan_changed": False},
                     metadata={"request_id": request_id, "customer_id": request.customer_id,
                               "workflow_id": request_id, "entity": "requests"},
                     tags=("database", "request"))
        return True

    def reconcile_interrupted_requests(self, current_executor_id: str) -> list[str]:
        """Fail Search/Design runs whose executor can no longer be alive.

        Runs at web startup. Only PROCESSING build-stage rows that are unowned
        (legacy) or owned by an earlier web boot are touched; CLI-owned rows,
        the current boot's rows, APPLYING and every waiting/terminal state are
        left alone. Safe to run repeatedly.
        """
        reconciled = []
        for request_id in self.database.abandoned_build_request_ids(current_executor_id):
            try:
                self.mark_error(request_id, allowed_from=BUILD_STATES, error_message=INTERRUPTED_MESSAGE)
            except InvalidLifecycleTransition:
                continue  # advanced since it was listed; never overwrite it
            reconciled.append(request_id)
        return reconciled

    def mark_error(self, request_id: str, *, allowed_from=ACTIVE_STATES,
                   error_message: str | None = None) -> None:
        """Technical failure is terminal and preserves every checkpoint output.

        allowed_from narrows which states may fail (e.g. BUILD_STATES only);
        CANCELLED/COMPLETED/ERROR are never sources.
        """
        self.database.transition_workflow(request_id, allowed_from=allowed_from,
                                          status=Status.ERROR, stage=Stage.ERROR)
        with closing(self.database._connect()) as connection, connection:
            connection.execute("UPDATE requests SET business_status='FAILED', completed_at=updated_at, "
                               "error_message=COALESCE(?, error_message) WHERE request_id=?",
                               (error_message, request_id))
        request = self.get_request(request_id)
        record_event("request.failed", inputs={"request_id": request_id},
                     outputs={"status": "FAILED", "workflow_status": request.status.value if request else "ERROR",
                              "workflow_stage": request.stage.value if request else "ERROR"},
                     metadata={"request_id": request_id,
                               "customer_id": request.customer_id if request else None,
                               "workflow_id": request_id, "entity": "requests"},
                     tags=("database", "request", "failure"))
