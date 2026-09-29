"""LangSmith experiment targets; live LLM targets are invoked only by eval CLI."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase
from backend.schemas.requirements import NeedLevel, NeedSelection, Requirements
from backend.schemas.search import AddonSearchInput, CandidatePool, PackageSearchInput, SearchStatus, search_needs_from_requirements
from backend.services.current_plan_service import CurrentPlanService
from backend.services.history_service import HistoryService
from backend.services.request_service import RequestService
from backend.tools.addon_search_tool import _search_addons_impl
from backend.tools.apply_tool import apply_selected_solution
from backend.tools.search_tool import _search_packages_impl
from backend.workflow.graph import build_workflow_graph
from backend.schemas.state import TELORAState
from backend.agents.search_agent import run_search_agent
from backend.agents.solution_design_agent import run_solution_design, assemble_final_solutions
from backend.services.tracing_service import sanitize


def _requirements(value: dict) -> Requirements:
    def item(key):
        row = value.get(key)
        return NeedSelection(row["need"], NeedLevel(row["level"])) if row else None
    return Requirements(item("primary_need"), item("secondary_need"), item("third_need"),
                        value["preferred_budget_sar"])


def search_target(inputs: dict) -> dict:
    requirements = _requirements(inputs["requirements"])
    state = run_search_agent(TELORAState(request_id="LANGSMITH-EVAL", user_requirements=requirements))
    trace = state.search_trace.to_dict() if state.search_trace else {}
    return {"candidate_pool": state.candidate_pool.model_dump(mode="json") if state.candidate_pool else None,
            "stages": trace.get("stages", []), "tool_traces": trace.get("tool_traces", [])}


def solution_design_target(inputs: dict) -> dict:
    requirements = _requirements(inputs["requirements"])
    pool = CandidatePool.model_validate(inputs["candidate_pool"])
    if not pool.package_candidates:
        return {"outcome": "NO_VALID_RECOMMENDATION", "solutions": [], "stages": [],
                "guardrails": [], "contract_result": "NO_PACKAGE_CANDIDATES"}
    result = run_solution_design(requirements, pool)
    final = assemble_final_solutions(pool, result).model_dump(mode="json")
    return {"outcome": result.outcome, "solutions": final.get("solutions", []),
            "stages": [stage.to_dict() for stage in result.stage_traces],
            "guardrails": [item.model_dump(mode="json") for item in result.guardrails]}


def _isolated_database():
    directory = tempfile.TemporaryDirectory(prefix="telora-ls-eval-")
    path = Path(directory.name) / "telora.sqlite3"
    shutil.copy2(DEFAULT_DATABASE_PATH, path)
    return directory, SearchDatabase(path)


def _apply_fixture(inputs: dict, *, run_graph: bool = False) -> dict:
    directory, database = _isolated_database()
    try:
        service = RequestService(database)
        customer_id = inputs["customer_id"]
        requirements = _requirements(inputs.get("requirements") or {
            "primary_need": {"need": "Data", "level": "Medium"},
            "secondary_need": None, "third_need": None, "preferred_budget_sar": 300,
        })
        request = service.start_customer_request(customer_id, requirements)
        if run_graph:
            build_workflow_graph(request_service=service).invoke({"request_id": request.request_id})
            checkpoint = service.get_checkpoint(request.request_id)
            solutions = (checkpoint.final_solutions or {}).get("solutions", []) if checkpoint else []
            if not solutions:
                return {"request_id": request.request_id, "workflow_status": service.get_request(request.request_id).status.value,
                        "workflow_stage": service.get_request(request.request_id).stage.value,
                        "solutions": [], "order_status": None, "request_status": "FAILED",
                        "error_message": "No final solution was produced"}
            selected = solutions[0]
            service.select_solution(request.request_id, selected)
        else:
            needs = search_needs_from_requirements(requirements)
            package_result = _search_packages_impl(PackageSearchInput(
                needs=needs, preferred_budget_sar=requirements.preferred_budget_sar), database.path)
            addon_result = _search_addons_impl(AddonSearchInput(
                needs=needs, preferred_budget_sar=requirements.preferred_budget_sar), database.path)
            pool = CandidatePool(package_candidates=package_result.candidates,
                                 addon_candidates=addon_result.candidates,
                                 search_status=SearchStatus.SUCCESS if package_result.candidates else SearchStatus.NO_RESULTS)
            package = next((item for item in pool.package_candidates
                            if item.package_id == inputs["package_id"]), None)
            if package is None:
                package = pool.package_candidates[0]
            service.search_succeeded(request.request_id, pool)
            selected = {"solution_id": "EVAL-SOLUTION-1", "package": package.model_dump(mode="json"),
                        "addons": [], "total_price_sar": package.price_sar,
                        "budget_status": "WITHIN_BUDGET" if package.price_sar <= requirements.preferred_budget_sar else "ABOVE_BUDGET",
                        "needs_coverage": [], "trade_off": "Evaluation fixture based on catalog price.",
                        "recommended": True, "recommendation_reason": "Evaluation fixture."}
            service.solution_design_ready(request.request_id, candidate_solutions=[selected],
                                          solution_validation=[{}], final_solutions={"solutions": [selected]})
            service.select_solution(request.request_id, selected)
            solutions = [selected]

        selected_id = selected["solution_id"]
        package_ids = [selected["package"]["package_id"]]
        addon_ids = [item["addon_id"] for item in selected.get("addons", [])]
        before_plan = CurrentPlanService(database).get_current_plan(customer_id)
        case_id = inputs.get("case_id", "")
        if inputs.get("invalid_package"):
            package_ids = ["__INVALID_PACKAGE_ID__"]
        if inputs.get("invalid_addon"):
            addon_ids = ["__INVALID_ADDON_ID__"]
        if inputs.get("catalog_mismatch"):
            package_ids = ["__DIFFERENT_PACKAGE_ID__"]
        if inputs.get("invalid_selection"):
            selected_id = "__INVALID_SOLUTION_ID__"

        def fail_activation():
            raise RuntimeError("Evaluation injected activation failure")

        result = apply_selected_solution(
            customer_id, request.request_id, selected_id,
            package_ids=package_ids, addon_ids=addon_ids,
            database=database,
            activation=fail_activation if not inputs.get("activation_succeeds", True) else None,
            require_saved_selection=True,
        )
        current_plan = CurrentPlanService(database).get_current_plan(customer_id)
        history = HistoryService(database)
        orders = history.get_customer_orders(customer_id)
        latest_order = next((row for row in orders if row.get("request_id") == request.request_id), None)
        details = history.get_customer_requests(customer_id)
        latest_request = next((row for row in details if row.get("request_id") == request.request_id), None)
        return {
            "case_id": case_id, "prediction": result,
            "workflow_status": service.get_request(request.request_id).status.value,
            "workflow_stage": service.get_request(request.request_id).stage.value,
            "before": {"current_plan": before_plan},
            "after": {"current_plan": current_plan,
                      "order_status": latest_order.get("status") if latest_order else None,
                      "request_status": latest_request.get("business_status") if latest_request else None,
                      "request_order_id": latest_request.get("order_id") if latest_request else None},
            "selected_solution": selected,
            # Every solution the run presented, so semantic graders can judge the choice offered.
            "solutions": solutions,
            "request_id": request.request_id,
        }
    finally:
        directory.cleanup()


def apply_target(inputs: dict) -> dict:
    return _apply_outputs(_apply_fixture(inputs))


def _apply_outputs(fixture: dict) -> dict:
    prediction = fixture["prediction"]
    return sanitize({**prediction, "case_id": fixture.get("case_id"), "before": fixture["before"],
            "after": fixture["after"], "workflow_status": fixture.get("workflow_status"),
            "workflow_stage": fixture.get("workflow_stage"), "request_id": fixture.get("request_id"),
            "selected_solution": fixture.get("selected_solution"),
            "solutions": fixture.get("solutions")})


def workflow_target(inputs: dict) -> dict:
    fixture = _apply_fixture(inputs, run_graph=True)
    return sanitize({key: value for key, value in fixture.items() if key not in {"before", "after"}})


def end_to_end_target(inputs: dict) -> dict:
    return _apply_outputs(_apply_fixture(inputs, run_graph=True))
