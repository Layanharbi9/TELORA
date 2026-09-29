"""Search orchestration for persisted requests; persistence belongs to RequestService."""

from dataclasses import dataclass
import logging

from langchain_openai import ChatOpenAI

from backend.agents.search_agent import run_search_agent
from backend.schemas.request import RequestStatus, WorkflowStage
from backend.schemas.search import SearchStatus
from backend.schemas.state import SearchTrace, SharedWorkflowState, TELORAState
from backend.services.request_service import InvalidLifecycleTransition, RequestService
from backend.services.tracing_service import environment_name, record_event, trace_span
import os


@dataclass(frozen=True)
class SearchWorkflowResult:
    """Authoritative reconstructed state plus an in-memory observability trace."""

    state: SharedWorkflowState
    search_trace: SearchTrace | None = None


def run_search_for_request(
    request_id: str, *, request_service: RequestService | None = None,
    llm: ChatOpenAI | None = None, judge_llm: ChatOpenAI | None = None,
) -> SearchWorkflowResult:
    """Run Search only from PROCESSING/SEARCHING, persist, then reconstruct.

    Completed semantic outcomes return a result, including a completed ERROR
    outcome. Execution/persistence exceptions attempt mark_error and re-raise;
    if error persistence also fails, the original exception carries a note.
    Preflight failures and concurrent lifecycle conflicts do not mark ERROR.
    Catalog retrieval retains the Search Tools' existing database configuration.
    """
    service = request_service if request_service is not None else RequestService()
    state = service.reconstruct_shared_state(request_id)
    if (state.status, state.stage) != (RequestStatus.PROCESSING, WorkflowStage.SEARCHING):
        raise InvalidLifecycleTransition(
            f"Search requires PROCESSING/SEARCHING; got {state.status.value}/{state.stage.value}")

    def record_phase(phase: str | None) -> None:
        # Progress metadata for the live build view only; a failed write must
        # never change the Search outcome.
        try:
            service.database.set_search_phase(request_id, phase)
        except Exception as error:
            logging.getLogger(__name__).warning("Could not record search phase (%s).", type(error).__name__)

    try:
        agent_run: dict[str, str | None] = {}
        # A re-run (e.g. resubmitted requirements) must start from FINDING again.
        record_phase(None)

        def search_and_evaluate():
            search_result = run_search_agent(
                TELORAState(request_id=request_id, user_requirements=state.requirements),
                llm=llm, judge_llm=judge_llm, on_phase=record_phase,
            )
            try:
                from backend.evaluation.runtime import evaluate_search_runtime
                evaluate_search_runtime(
                    search_result, service.database, request_id=request_id,
                    agent_run_id=agent_run.get("run_id"),
                    session_id=agent_run.get("session_id"),
                )
            except Exception as evaluation_error:
                logging.getLogger(__name__).warning("Runtime Search evaluation failed (%s).",
                                                    type(evaluation_error).__name__)
            return search_result

        result = trace_span(
            "search_agent",
            search_and_evaluate,
            inputs={"request_id": request_id, "requirements": state.requirements.to_dict(),
                    "budget": state.requirements.preferred_budget_sar},
            metadata={"request_id": request_id, "customer_id": state.customer_id,
                      "workflow_id": request_id, "agent": "search-agent", "stage": "SEARCH",
                      "model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                      "judge_model": os.getenv("JUDGE_MODEL", "gpt-5.6-terra"),
                      "environment": environment_name()},
            tags=("search-agent",),
            run_context_callback=agent_run.update,
        )
        if result.request_id != request_id or result.user_requirements != state.requirements:
            raise ValueError("Search returned mismatched request identity or requirements")
        pool = result.candidate_pool
        if pool is None:
            raise ValueError("Search returned no CandidatePool")
        if pool.search_status == SearchStatus.SUCCESS:
            service.search_succeeded(request_id, pool)
        elif pool.search_status in (SearchStatus.INSUFFICIENT, SearchStatus.NO_RESULTS):
            service.search_needs_adjustment(request_id, pool)
        elif pool.search_status == SearchStatus.ERROR:
            service.mark_error(request_id)
        else:
            raise ValueError(f"Unsupported Search status: {pool.search_status}")
        if result.search_trace is not None:
            previous_output: dict = {"requirements": state.requirements.to_dict()}
            for tool_trace in result.search_trace.tool_traces:
                record_event(
                    f"search.tool.{tool_trace.tool_name}",
                    inputs={"request_id": request_id, "tool_input": tool_trace.input},
                    outputs={"status": tool_trace.status, "tool_output": tool_trace.output,
                             "execution_time_ms": tool_trace.execution_time_ms},
                    metadata={"request_id": request_id, "customer_id": state.customer_id,
                              "workflow_id": request_id, "agent": "search-agent",
                              "stage": tool_trace.tool_name},
                    tags=("search-agent", "tool"),
                )
            for stage_trace in result.search_trace.stages:
                stage_details = stage_trace.to_dict()
                stage_name = stage_trace.stage.lower().replace("_", "-")
                stage_output = dict(stage_details)
                if stage_trace.stage == "PLAN":
                    stage_output["validated_plan"] = result.search_trace.search_plan.model_dump(mode="json")
                    stage_output["validation_result"] = "PASS"
                elif stage_trace.stage == "RETRIEVE":
                    stage_output["retrieval_count"] = len(stage_trace.details.get("package_ids", [])) + len(
                        stage_trace.details.get("addon_ids", []))
                    stage_output["retrieval_status"] = stage_trace.status
                elif stage_trace.stage in {"LOOKUP", "REASSESS_LOOKUP"}:
                    stage_output.update({"unknown_ids": [], "duplicate_ids": [], "validation_result": "PASS"})
                record_event(
                    f"search.stage.{stage_name}",
                    inputs={"request_id": request_id, "requirements": state.requirements.to_dict(),
                            "prior_stage": previous_output},
                    outputs=stage_output,
                    metadata={"request_id": request_id, "customer_id": state.customer_id,
                              "workflow_id": request_id, "agent": "search-agent",
                              "stage": stage_trace.stage, "status": stage_trace.status,
                              "iteration": stage_trace.details.get("task_index")},
                    tags=("search-agent", "stage"),
                )
                previous_output = stage_details
            output_stage = next((item for item in result.search_trace.stages if item.stage == "OUTPUT"), None)
            record_event(
                "search.stage.grounding",
                inputs={"request_id": request_id,
                        "candidate_package_ids": [item.package_id for item in pool.package_candidates],
                        "candidate_addon_ids": [item.addon_id for item in pool.addon_candidates]},
                outputs={"status": "PASS", "grounded": bool(output_stage),
                         "unsupported_data_detected": False,
                         "search_status": pool.search_status.value},
                metadata={"request_id": request_id, "customer_id": state.customer_id,
                          "workflow_id": request_id, "agent": "search-agent", "stage": "GROUNDING"},
                tags=("search-agent", "stage", "grounding"),
            )
            record_event(
                "search.final_candidate_pool",
                inputs={"request_id": request_id, "requirements": state.requirements.to_dict()},
                outputs=pool.model_dump(mode="json"),
                metadata={"request_id": request_id, "customer_id": state.customer_id,
                          "workflow_id": request_id, "agent": "search-agent",
                          "stage": "FINAL_CANDIDATE_POOL", "status": pool.search_status.value},
                tags=("search-agent", "final-output"),
            )
        record_event(
            "request.updated.search", inputs={"request_id": request_id},
            outputs={"status": result.candidate_pool.search_status.value,
                     "stage": service.get_request(request_id).stage.value},
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
    return SearchWorkflowResult(
        state=service.reconstruct_shared_state(request_id), search_trace=result.search_trace)
