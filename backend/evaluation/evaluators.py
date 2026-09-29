"""LangSmith callable evaluators mapped to named TELORA rubrics."""

from __future__ import annotations

from backend.evaluation.rubrics import (
    APPLY_RUBRICS, FINAL_RUBRICS, SEARCH_RUBRICS, SOLUTION_RUBRICS,
    WORKFLOW_RUBRICS, evaluate_apply_contract, evaluate_search_contract,
    evaluate_solution_contract, metric_result, strict_llm_rubric,
)


def _io(run, example):
    outputs = getattr(run, "outputs", None) or {}
    reference = getattr(example, "outputs", None) or {}
    return outputs, reference


def _batch(results):
    return {"results": results}


def search_deterministic(run, example):
    outputs, reference = _io(run, example)
    return _batch(list(evaluate_search_contract(outputs, reference).values()))


def solution_deterministic(run, example):
    outputs, reference = _io(run, example)
    return _batch(evaluate_solution_contract(outputs, reference))


def apply_deterministic(run, example):
    outputs, reference = _io(run, example)
    before, after = outputs.get("before", {}), outputs.get("after", {})
    return _batch(evaluate_apply_contract(outputs, reference, before, after))


def workflow_deterministic(run, example):
    outputs, reference = _io(run, example)
    status = outputs.get("workflow_status")
    prediction = outputs.get("prediction", {})
    after = outputs.get("after", {})
    request_order_matches = bool(after.get("request_order_id")) == bool(prediction.get("order_id"))
    expected = reference.get("expected_terminal_status")
    expected_request_status = "COMPLETED" if expected == "ACTIVE" else "ERROR"
    expected_stage = "COMPLETED" if expected == "ACTIVE" else "ERROR"
    correct_final = status == expected_request_status and prediction.get("status") == expected
    stage_ok = outputs.get("workflow_stage") == expected_stage
    return _batch([
        metric_result("stage_transitions", stage_ok, "Final request is at COMPLETED/COMPLETED." if stage_ok else "Request did not reach COMPLETED stage."),
        metric_result("request_order_relationship", request_order_matches, "Request and returned order references agree." if request_order_matches else "Request/order references do not agree."),
        metric_result("final_state", correct_final, "Persisted workflow and Apply result match expected terminal status." if correct_final else "Workflow terminal state differs from expected status."),
    ])


def final_system_deterministic(run, example):
    outputs, reference = _io(run, example)
    before, after = outputs.get("before", {}), outputs.get("after", {})
    result = outputs.get("prediction", outputs)
    expected = reference.get("expected_terminal_status")
    passed = result.get("status") == expected
    if expected == "ACTIVE":
        passed &= before.get("current_plan") != after.get("current_plan")
    elif expected == "FAILED":
        passed &= before.get("current_plan") == after.get("current_plan")
    passed &= bool(after.get("request_order_id")) == bool(result.get("order_id"))
    return metric_result("final_state_consistency", bool(passed),
                         "Final response, request/order references, and current plan are consistent."
                         if passed else "Final response and persisted database state disagree.")


def build_evaluators(dataset_name: str, *, llm=None):
    """Return deterministic metrics for every dataset plus strict semantic graders."""
    mapping = {
        "search_agent_eval": [search_deterministic],
        "solution_design_eval": [solution_deterministic],
        "apply_tool_eval": [apply_deterministic],
        "workflow_eval": [workflow_deterministic],
        "end_to_end_eval": [workflow_deterministic, final_system_deterministic],
    }
    evaluators = mapping[dataset_name]
    if llm is not None:
        scope, rubrics = {
            "search_agent_eval": ("search", SEARCH_RUBRICS),
            "solution_design_eval": ("solution", SOLUTION_RUBRICS),
            "end_to_end_eval": ("final", FINAL_RUBRICS),
        }.get(dataset_name, (None, {}))
        for metric, rubric in rubrics.items():
            if rubric["evaluator_type"] == "llm_semantic":
                evaluators.append(strict_llm_rubric(llm, metric=metric, rubric=rubric, scope=scope))
    return evaluators
