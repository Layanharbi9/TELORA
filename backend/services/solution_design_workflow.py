"""Solution Design orchestration for persisted requests; persistence belongs to RequestService."""

from dataclasses import dataclass
import logging

from langchain_openai import ChatOpenAI

from backend.agents.solution_design_agent import SolutionPipelineResult, assemble_final_solutions, run_solution_design
from backend.schemas.request import RequestStatus, WorkflowStage
from backend.schemas.state import SharedWorkflowState
from backend.services.request_service import InvalidLifecycleTransition, RequestService
from backend.services.tracing_service import environment_name, record_event, trace_span
import os


@dataclass(frozen=True)
class SolutionDesignWorkflowResult:
    """Authoritative reconstructed state plus the in-memory pipeline result."""

    state: SharedWorkflowState
    pipeline_result: SolutionPipelineResult


def run_solution_design_for_request(
    request_id: str, *, request_service: RequestService | None = None,
    llm: ChatOpenAI | None = None,
) -> SolutionDesignWorkflowResult:
    """Run Solution Design only from PROCESSING/DESIGNING, persist once, then reconstruct.

    run_solution_design() and assemble_final_solutions() are called exactly once. No
    VALIDATING/PREPARING_SOLUTIONS checkpoint is persisted --
    candidate_solutions, solution_validation, and final_solutions are written
    together in the single transition their outcome selects. Execution/persistence
    exceptions attempt mark_error and re-raise; if error persistence also fails,
    the original exception carries a note. Preflight failures and concurrent
    lifecycle conflicts do not mark ERROR.
    """
    service = request_service if request_service is not None else RequestService()
    state = service.reconstruct_shared_state(request_id)
    if (state.status, state.stage) != (RequestStatus.PROCESSING, WorkflowStage.DESIGNING):
        raise InvalidLifecycleTransition(
            f"Solution Design requires PROCESSING/DESIGNING; got {state.status.value}/{state.stage.value}")
    if state.candidate_pool is None:
        raise ValueError("Solution Design requires a persisted CandidatePool")

    try:
        # Search has already persisted the CandidatePool; its candidates carry every
        # catalog fact Solution Design needs.
        candidate_ids = {
            "package_ids": [item.package_id for item in state.candidate_pool.package_candidates],
            "addon_ids": [item.addon_id for item in state.candidate_pool.addon_candidates],
        }
        current_plan = None
        try:
            from backend.services.current_plan_service import CurrentPlanService
            plan = CurrentPlanService(service.database).get_current_plan(state.customer_id)
            if plan:
                current_plan = {key: plan.get(key) for key in
                                ("customer_id", "package_id", "package_name", "addon_ids",
                                 "addon_names", "total_price", "updated_at")}
        except Exception:
            # Current plan is contextual observability only; it does not gate
            # recommendation generation.
            current_plan = None
        agent_run: dict[str, str | None] = {}

        def design_and_evaluate():
            pipeline = run_solution_design(state.requirements, state.candidate_pool, llm=llm)
            final_output = assemble_final_solutions(state.candidate_pool, pipeline).model_dump(mode="json")
            try:
                from backend.evaluation.runtime import evaluate_solution_runtime
                evaluate_solution_runtime(
                    state.requirements, state.candidate_pool, final_output, service.database,
                    request_id=request_id, agent_run_id=agent_run.get("run_id"),
                    session_id=agent_run.get("session_id"),
                )
            except Exception as evaluation_error:
                logging.getLogger(__name__).warning("Runtime Solution Design evaluation failed (%s).",
                                                    type(evaluation_error).__name__)
            return pipeline, final_output

        result, final_solutions = trace_span(
            "solution_design_agent",
            design_and_evaluate,
            inputs={"request_id": request_id, "requirements": state.requirements.to_dict(),
                    "budget": state.requirements.preferred_budget_sar,
                    "candidate_pool": state.candidate_pool.model_dump(mode="json"),
                    "previous_current_plan": current_plan},
            metadata={"request_id": request_id, "customer_id": state.customer_id,
                      "workflow_id": request_id, "agent": "solution-design", "stage": "DESIGN",
                      "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                      "design_model": os.getenv("DESIGN_MODEL", "gpt-5.6-terra"),
                      "environment": environment_name()},
            tags=("solution-design",),
            run_context_callback=agent_run.update,
        )
        for stage in result.stage_traces:
            record_event(
                f"solution_design.stage.{stage.stage.lower().replace('_', '-')}",
                inputs={"request_id": request_id, "candidate_ids": candidate_ids},
                outputs=stage.to_dict(),
                metadata={"request_id": request_id, "customer_id": state.customer_id,
                          "workflow_id": request_id, "agent": "solution-design",
                          "stage": stage.stage, "status": stage.status},
                tags=("solution-design", "stage"),
            )
        candidate_solutions = [solution.model_dump(mode="json") for solution in result.solutions]
        solution_validation = [check.model_dump(mode="json") for check in result.guardrails]
        if result.outcome == "RECOMMENDED":
            service.solution_design_ready(request_id, candidate_solutions=candidate_solutions,
                                          solution_validation=solution_validation, final_solutions=final_solutions)
        elif result.outcome == "NO_VALID_RECOMMENDATION":
            service.solution_design_needs_adjustment(
                request_id, candidate_solutions=candidate_solutions,
                solution_validation=solution_validation, final_solutions=final_solutions)
        else:
            raise ValueError(f"Unsupported Solution Design outcome: {result.outcome}")
        record_event(
            "solution_design.final_solutions",
            inputs={"request_id": request_id, "candidate_ids": candidate_ids},
            outputs={"outcome": result.outcome, "solutions": final_solutions.get("solutions", []),
                     "guardrails": solution_validation,
                     "comparison_status": result.comparison_status,
                     "recommendation_status": result.recommendation_status},
            metadata={"request_id": request_id, "customer_id": state.customer_id,
                      "workflow_id": request_id, "agent": "solution-design",
                      "stage": "FINAL_OUTPUT", "status": result.outcome},
            tags=("solution-design", "final-output"),
        )
        request_after = service.get_request(request_id)
        record_event(
            "request.updated.solution-design", inputs={"request_id": request_id},
            outputs={"status": request_after.status.value, "stage": request_after.stage.value,
                     "solution_count": len(final_solutions.get("solutions", []))},
            metadata={"request_id": request_id, "customer_id": state.customer_id,
                      "workflow_id": request_id, "entity": "requests"},
            tags=("database", "request"),
        )
    except InvalidLifecycleTransition:
        # Another caller may already have advanced this request. Never overwrite it.
        raise
    except Exception as error:
        try:
            service.mark_error(request_id)
        except Exception as persistence_error:
            error.add_note(f"Could not persist ERROR lifecycle: {type(persistence_error).__name__}")
        raise

    # A read failure here must not undo/overwrite an already committed transition.
    return SolutionDesignWorkflowResult(
        state=service.reconstruct_shared_state(request_id), pipeline_result=result)
