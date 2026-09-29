"""Explicit TELORA metric definitions and reusable deterministic evaluators.

Every deterministic result uses binary scoring: 1.0 only when its stated PASS
criterion is evidenced in the structured prediction; otherwise 0.0. Semantic
LLM evaluators return a constrained 0..1 score and a short evidence statement.
"""

from __future__ import annotations

from typing import Any


def _rubric(definition, expected, pass_criteria, fail_criteria, evidence, evaluator_type):
    return {"definition": definition, "expected_behavior": expected,
            "pass_criteria": pass_criteria, "fail_criteria": fail_criteria,
            "scoring_rule": "binary 1.0 PASS / 0.0 FAIL" if evaluator_type == "deterministic"
                            else "LLM assigns 0.0, 0.5, or 1.0 using the stated evidence rubric",
            "evidence": evidence, "evaluator_type": evaluator_type}


SEARCH_RUBRICS = {
    "relevance": _rubric("Candidates support at least one requested need.", "Every returned candidate is relevant to a requested need.", "Each candidate has a nonempty matched_needs subset of original needs.", "Any candidate is unrelated or refers to an unknown need.", "Requirements and CandidatePool.matched_needs", "llm_semantic"),
    "needs_coverage": _rubric("The final pool covers requested needs when catalog evidence permits.", "Coverage evidence is limited to canonical matched needs and capabilities.", "Pool candidates collectively match each need for which the canonical retrieval results contain a match.", "A need is claimed covered without candidate evidence.", "Original requirements and canonical candidate fields", "deterministic"),
    "diversity": _rubric("The pool retains meaningfully distinct candidate choices.", "Non-dominated options are not collapsed into one without evidence.", "Unique candidate IDs are preserved and useful alternatives remain where supplied.", "Duplicates appear or all valid alternatives are lost.", "Retrieved and final candidate IDs plus capabilities", "llm_semantic"),
    "retrieval_correctness": _rubric("Search output IDs come from the tool retrieval output.", "Canonical lookup resolves exactly selected IDs.", "Every final ID exists in the corresponding original tool result.", "Unknown or cross-kind IDs appear.", "ToolTrace outputs and final CandidatePool", "deterministic"),
    "candidate_id_validity": _rubric("Package and add-on IDs belong to the right catalog.", "IDs are type-correct and unique.", "All package IDs resolve as packages and add-on IDs as add-ons.", "Unknown, duplicated, or cross-catalog IDs appear.", "SQLite package/addon ID sets", "deterministic"),
    "canonical_integrity": _rubric("Candidate names, prices, and capabilities match SQLite.", "No LLM-created catalog facts survive grounding.", "All structured candidate fields equal canonical rows.", "Any candidate fact differs from its canonical catalog record.", "SQLite records and CandidatePool", "deterministic"),
    "groundedness": _rubric("Search explanations are supported by retrieved evidence.", "Notes describe only evidenced matches/gaps.", "Each stated capability maps to a canonical candidate field.", "Unsupported benefits or capabilities are asserted.", "Structured search notes and canonical catalog", "llm_semantic"),
    "insufficient_result_handling": _rubric("Search reports insufficient/no results accurately.", "Empty/limited retrieval is not described as a successful full match.", "Status follows result availability and validated judge outcome.", "Empty results are called SUCCESS or unsupported completeness is claimed.", "SearchStatus, tool outputs, judge verdict", "deterministic"),
    "reassessment_correctness": _rubric("At most one semantic reassessment uses original retrieved candidates.", "REASSESS follows an INSUFFICIENT Judge only; no second retrieval occurs.", "At most one REASSESS; original pool IDs bound the output; RETRIEVE count remains one.", "Reassessment is untriggered, repeated, or introduces new IDs/retrieval.", "SearchTrace ordered stages and original retrieval IDs", "deterministic"),
    "runtime_judge_correctness": _rubric("The in-workflow Judge verdict and gaps are justified by the canonical shortlist.", "Gaps reference real requested needs and the verdict is consistent with them.", "SUFFICIENT has no gaps; INSUFFICIENT has evidence-backed gaps using valid priority references; reassessment triggers only for INSUFFICIENT.", "Verdict conflicts with gaps, a gap references no requested need, or evidence does not support the gap.", "Requirements, canonical CandidatePool, structured Judge output, and SearchTrace", "llm_semantic"),
    "no_hallucinated_catalog_data": _rubric("No package/add-on fact is invented.", "Catalog facts are canonical SQLite values.", "Every output component fact matches a catalog row.", "Unknown component or unsupported price/capability appears.", "SQLite catalog and CandidatePool", "deterministic"),
}

SOLUTION_RUBRICS = {
    "requirements_alignment": _rubric("Solutions respond to the customer's declared needs.", "Coverage aligns with each original need and priority.", "Each solution addresses the supplied requirements without introducing new needs.", "A required need is omitted or invented.", "Requirements and solution needs_coverage", "llm_semantic"),
    "needs_coverage": _rubric("Coverage entries accurately describe candidate capability.", "Coverage evidence is supported by selected canonical package/add-ons.", "Every COVERED claim has matching capability evidence.", "Unsupported coverage or missing requirement entries.", "CandidatePool and NeedsCoverage evidence", "llm_semantic"),
    "budget_compliance": _rubric("Total price is compared with the supplied preference.", "Budget is a soft preference, explicitly and accurately reported.", "Reported price equals canonical price sum and status matches its comparison to budget.", "Price differs from catalog sum or budget status contradicts values.", "SQLite prices, output total, preferred budget", "deterministic"),
    "solution_completeness": _rubric("Each solution has one package, valid add-ons, price, coverage, trade-off and recommendation fields.", "Final solution schema is complete.", "Required fields exist and component IDs are from CandidatePool.", "Any required field is missing or composition is invalid.", "FinalSolution schema and CandidatePool", "deterministic"),
    "groundedness": _rubric("Benefits and explanations use retrieved/canonical evidence.", "Unsupported details are identified as unavailable.", "Every factual explanation is supported by candidate or retrieved context.", "A claim has no supporting evidence.", "Final output, CandidatePool, retrieval context", "llm_semantic"),
    "constraint_satisfaction": _rubric("Composition respects the add-on limit and selected candidate pool.", "No unknown components are proposed.", "IDs are in CandidatePool and canonical composition passes validation.", "Any membership or composition constraint is violated.", "SQLite composition rules and CandidatePool", "deterministic"),
    "internal_consistency": _rubric("Explanation, price, budget label and coverage agree.", "No field contradicts another output field.", "All duplicated/derived facts reconcile.", "Any inconsistency is found.", "Structured final solution and canonical calculations", "deterministic"),
    "solution_quality": _rubric("Alternatives expose meaningful, evidenced trade-offs.", "Solutions differ on a customer-relevant factor or say they are equivalent.", "Explanation distinguishes choices with evidence and reflects prioritized needs.", "Difference is fabricated or explanation ignores the request.", "Requirements, alternatives, trade_off and comparison", "llm_semantic"),
}

APPLY_RUBRICS = {
    key: _rubric(f"Apply invariant: {key.replace('_', ' ')}.", f"{key.replace('_', ' ')} is enforced by the deterministic application service.",
                 f"Structured result and resulting SQLite rows prove {key.replace('_', ' ')}.",
                 f"The invariant {key.replace('_', ' ')} is violated or cannot be evidenced.",
                 "Apply input/result and SQLite before/after rows", "deterministic")
    for key in ("input_validation", "customer_validation", "solution_validation", "package_validation",
                "addon_validation", "price_correctness", "previous_plan_preservation", "order_correctness",
                "current_plan_update_correctness", "request_history_correctness", "transaction_integrity",
                "failure_rollback")
}
WORKFLOW_RUBRICS = {
    key: _rubric(f"Workflow invariant: {key.replace('_', ' ')}.", f"{key.replace('_', ' ')} matches the persisted lifecycle.",
                 f"SQLite state and traced stage events verify {key.replace('_', ' ')}.",
                 f"A transition, handoff, required field, or final relationship is invalid.",
                 "LangGraph stage events and SQLite request/order/current plan", "deterministic")
    for key in ("stage_transitions", "state_propagation", "agent_handoff", "tool_invocation",
                "required_inputs", "unexpected_outputs", "request_order_relationship", "final_state")
}
# End-to-end metrics grade the solutions a full run presented to the customer.
# Semantic ones reuse the Solution Design criteria (see judge_prompts.py).
FINAL_RUBRICS = {
    "relevance": _rubric("Presented solutions answer the customer's declared needs.", "Each solution serves the priority 1 need and addresses the others.", "Every solution serves priority 1 well and adds nothing unrequested.", "A solution misses priority 1 or is built around an unrequested need.", "Requirements and presented solutions with component capabilities", "llm_semantic"),
    "needs_coverage": _rubric("Coverage labels on presented solutions are true.", "COVERED is backed by a component capability at a sensible level.", "Every COVERED label has supporting capability; NOT_COVERED labels are correct.", "A COVERED label lacks capability or a requested need is missing.", "Presented solutions, coverage entries and component capabilities", "llm_semantic"),
    "diversity": _rubric("The customer is offered real alternatives.", "Presented solutions differ on price, allowance or need served, or a single one is explained.", "Two or more meaningfully different solutions, or one with a stated reason.", "Identical compositions are presented as different choices.", "Presented solution compositions and prices", "llm_semantic"),
    "groundedness": _rubric("Explanations shown to the customer are supported by data.", "Reasons and trade-offs match listed prices, allowances and solution count.", "Every factual statement matches the listed data.", "A statement contradicts listed prices, allowances or solutions.", "Recommendation reason, trade-offs, coverage evidence and component data", "llm_semantic"),
    "solution_quality": _rubric("The presented set and recommendation help the customer decide.", "Trade-offs name real differences; the reason is specific and free of internal labels.", "Distinct choices, specific trade-offs, customer-facing reason without internal labels.", "Duplicate choices, or a misleading or contradicting reason.", "Presented solutions, trade-offs and recommendation reason", "llm_semantic"),
    "budget_compliance": _rubric("Final price and budget label agree.", "The applied total equals the catalog sum and its budget label matches.", "Applied total equals catalog prices and the label matches the budget comparison.", "Price or budget label disagrees with catalog or budget.", "Applied order total, catalog prices, preferred budget", "deterministic"),
    "constraint_satisfaction": _rubric("The applied composition is valid.", "One package and at most two add-ons, all from the catalog.", "Applied IDs exist in the catalog and respect the add-on limit.", "An unknown component or too many add-ons.", "Applied order and SQLite catalog", "deterministic"),
    "final_state_consistency": _rubric("Response and database agree after the run.", "Request, order and current plan reflect the same outcome.", "Status, order link and current plan match the expected terminal state.", "Any of them disagrees with the returned result.", "Final response and SQLite request/order/current plan", "deterministic"),
}
def metric_result(key: str, passed: bool, evidence: str) -> dict[str, Any]:
    return {"key": key, "score": 1.0 if passed else 0.0,
            "value": "PASS" if passed else "FAIL", "comment": evidence}


def _value(source: Any, key: str, default=None):
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def evaluate_search_contract(outputs: dict, reference: dict) -> dict:
    pool = outputs.get("candidate_pool", outputs)
    original_packages = set(reference.get("retrieved_package_ids", []))
    original_addons = set(reference.get("retrieved_addon_ids", []))
    packages = _value(pool, "package_candidates", []) or []
    addons = _value(pool, "addon_candidates", []) or []
    package_ids = [_value(row, "package_id") for row in packages]
    addon_ids = [_value(row, "addon_id") for row in addons]
    valid = (len(package_ids) == len(set(package_ids)) and len(addon_ids) == len(set(addon_ids))
             and (not original_packages or set(package_ids) <= original_packages)
             and (not original_addons or set(addon_ids) <= original_addons))
    stages = outputs.get("stages", [])
    names = [_value(stage, "stage") for stage in stages]
    reassess_count = names.count("REASSESS")
    retrieval_count = names.count("RETRIEVE")
    reassess_ok = reassess_count <= 1 and retrieval_count == 1
    package_catalog = reference.get("package_catalog", {})
    addon_catalog = reference.get("addon_catalog", {})
    canonical_ok = True
    for row in packages:
        item_id = _value(row, "package_id")
        canonical = package_catalog.get(item_id)
        capabilities = _value(row, "capabilities", {}) or {}
        canonical_ok &= bool(canonical) and _value(row, "name") == canonical.get("name")
        if canonical:
            canonical_ok &= float(_value(row, "price_sar", 0)) == float(canonical.get("price_sar", 0))
            canonical_ok &= all(capabilities.get(key) == value
                                for key, value in canonical.get("capabilities", {}).items()
                                if key in capabilities)
    addon_aliases = {"eligible_for": "eligibility"}
    for row in addons:
        item_id = _value(row, "addon_id")
        canonical = addon_catalog.get(item_id)
        capabilities = _value(row, "capabilities", {}) or {}
        canonical_ok &= bool(canonical) and _value(row, "name") == canonical.get("name")
        if canonical:
            canonical_ok &= float(_value(row, "price_sar", 0)) == float(canonical.get("price_sar", 0))
            canonical_caps = canonical.get("capabilities", {})
            canonical_ok &= all(capabilities.get(key) == canonical_caps.get(addon_aliases.get(key, key))
                                for key in capabilities if key in canonical_caps or key in addon_aliases)
    judge = next((_value(stage, "details", {}) for stage in stages if _value(stage, "stage") == "JUDGE"), {})
    verdict = judge.get("verdict")
    gaps = judge.get("gaps", [])
    judge_structure_ok = ((verdict == "SUFFICIENT" and not gaps)
                          or (verdict == "INSUFFICIENT" and bool(gaps))) if judge else True
    if judge:
        judge_structure_ok &= reassess_count == (1 if verdict == "INSUFFICIENT" else 0)
    return {"candidate_id_validity": metric_result("candidate_id_validity", valid,
            "Unique final IDs are confined to supplied retrieval IDs." if valid else "Duplicates or IDs outside retrieval outputs were found."),
            "reassessment_correctness": metric_result("reassessment_correctness", reassess_ok,
            "SearchTrace has at most one REASSESS and one RETRIEVE." if reassess_ok else "SearchTrace exceeds reassessment/retrieval bounds."),
            "runtime_judge_contract": metric_result("runtime_judge_contract", judge_structure_ok,
            "Judge verdict, gaps, and reassessment trigger are structurally consistent." if judge_structure_ok else "Judge verdict/gaps or reassessment trigger conflict."),
            "canonical_integrity": metric_result("canonical_integrity", canonical_ok,
            "Candidate names, prices, and capabilities match catalog references." if canonical_ok else "A candidate field differs from canonical catalog data.")}


def evaluate_solution_contract(outputs: dict, reference: dict) -> list[dict]:
    solutions = outputs.get("solutions", []) if isinstance(outputs, dict) else []
    budget = reference.get("budget_sar")
    package_prices = reference.get("package_prices", {})
    addon_prices = reference.get("addon_prices", {})
    candidate_packages = set(reference.get("candidate_package_ids", package_prices))
    candidate_addons = set(reference.get("candidate_addon_ids", addon_prices))
    complete = True
    budget_ok = True
    ids_ok = True
    consistent = True
    for solution in solutions:
        package = solution.get("package", {})
        package_id = package.get("package_id")
        addon_ids = [item.get("addon_id") for item in solution.get("addons", [])]
        total = solution.get("total_price_sar")
        complete &= bool(solution.get("solution_id") and package_id and "needs_coverage" in solution
                         and "trade_off" in solution and "budget_status" in solution and "recommended" in solution)
        ids_ok &= package_id in candidate_packages and set(addon_ids) <= candidate_addons
        expected_total = package_prices.get(package_id)
        if expected_total is not None:
            expected_total += sum(addon_prices.get(addon_id, 0) for addon_id in addon_ids)
            consistent &= abs(float(total) - float(expected_total)) < 1e-6
        if budget is not None:
            within = str(solution.get("budget_status", "")).startswith(("WITHIN_BUDGET", "WITHIN"))
            budget_ok &= ((total <= budget) if within
                          else (total > budget))
    return [metric_result("solution_completeness", complete, "Required solution fields exist." if complete else "A required final solution field is missing."),
            metric_result("constraint_satisfaction", ids_ok, "All component IDs are within the canonical candidate set." if ids_ok else "A component ID is unknown to the candidate set."),
            metric_result("budget_compliance", budget_ok, "Budget labels agree with supplied prices and preference." if budget_ok else "Budget label contradicts total or budget."),
            metric_result("internal_consistency", consistent, "Final prices equal canonical package/add-on sums." if consistent else "A solution price differs from canonical catalog pricing.")]


def evaluate_apply_contract(prediction: dict, reference: dict, before: dict, after: dict) -> list[dict]:
    successful = prediction.get("status") == "ACTIVE" and prediction.get("success") is True
    failed = prediction.get("status") == "FAILED" and prediction.get("success") is False
    current_unchanged = before.get("current_plan") == after.get("current_plan")
    current_changed = before.get("current_plan") != after.get("current_plan")
    status_expected = reference.get("expected_success", reference.get("activation_succeeds", True))
    case_id = reference.get("case_id", prediction.get("case_id"))
    error_before_order = case_id in {"invalid_selection", "catalog_mismatch", "invalid_package", "invalid_addon"}
    new_plan = prediction.get("new_plan") or {}
    previous_plan = prediction.get("previous_plan") or {}
    current = after.get("current_plan") or {}
    current_package_ids = ([current.get("package_id")] if current.get("package_id") else [])
    current_addon_ids = current.get("addon_ids", [])
    current_price = current.get("total_price")
    validation_ok = successful or (
        failed and prediction.get("failure_recorded")
        and (not error_before_order or prediction.get("order_id") is None)
    )
    expected_price = reference.get("expected_total_price")
    if expected_price is None:
        expected_price = reference.get("total_price_sar")
    price_ok = (expected_price is None or prediction.get("total_price") == expected_price)
    if new_plan and prediction.get("total_price") is not None:
        price_ok &= new_plan.get("total_price") == prediction.get("total_price")
    previous_ids = previous_plan.get("package_ids", [])
    previous_preserved = (not successful and current_unchanged) or (
        successful and previous_ids == [before.get("current_plan", {}).get("package_id")]
        and previous_plan.get("total_price") == before.get("current_plan", {}).get("total_price"))
    order_ok = ((successful and bool(prediction.get("order_id")) and after.get("order_status") == "ACTIVE")
                or (failed and (after.get("order_status") == "FAILED" if not error_before_order
                                else after.get("order_status") in (None, "FAILED"))))
    plan_ok = ((successful and current_package_ids == new_plan.get("package_ids", [])
                and current_addon_ids == new_plan.get("addon_ids", [])
                and current_price == new_plan.get("total_price"))
               or (failed and current_unchanged))
    request_ok = ((successful and after.get("request_status") == "ACTIVE"
                   and after.get("request_order_id") == prediction.get("order_id"))
                  or (failed and after.get("request_status") == "FAILED"))
    integrity = (successful and current_changed and after.get("order_status") == "ACTIVE"
                 and after.get("request_status") == "ACTIVE") if status_expected else (
                 failed and current_unchanged and after.get("request_status") == "FAILED"
                 and (after.get("order_status") in (None, "FAILED") if error_before_order
                      else after.get("order_status") == "FAILED"))
    return [metric_result("input_validation", validation_ok, "Apply result matches the expected input-validation outcome." if validation_ok else "Apply accepted invalid input or failed to validate valid input."),
            metric_result("customer_validation", validation_ok, "Customer ownership/existence validation matched the fixture." if validation_ok else "Customer validation did not match the expected outcome."),
            metric_result("solution_validation", validation_ok, "Selected solution validation matched the expected outcome." if validation_ok else "Selected solution validation failed."),
            metric_result("package_validation", validation_ok, "Package validation matched the expected outcome." if validation_ok else "Package validation failed."),
            metric_result("addon_validation", validation_ok, "Add-on validation matched the expected outcome." if validation_ok else "Add-on validation failed."),
            metric_result("price_correctness", price_ok, "Resulting prices agree with the expected/canonical plan snapshot." if price_ok else "Apply total differs from the expected plan price."),
            metric_result("previous_plan_preservation", previous_preserved, "Previous plan snapshot matches the pre-apply current plan." if previous_preserved else "Previous plan snapshot was lost or changed."),
            metric_result("order_correctness", order_ok, "Order ID and terminal order status agree with the Apply outcome." if order_ok else "Order state does not agree with the Apply result."),
            metric_result("current_plan_update_correctness", plan_ok, "Current Plan matches the applied solution or stayed unchanged on failure." if plan_ok else "Current Plan differs from the applied solution or changed on failure."),
            metric_result("request_history_correctness", request_ok, "Request history records the matching terminal status and order." if request_ok else "Request history does not match the Apply result."),
            metric_result("transaction_integrity", integrity,
            "Order/request/current plan agree with activation outcome." if integrity else "Persisted order/request/current plan are inconsistent."),
            metric_result("failure_rollback", current_unchanged if not status_expected else True,
            "Failure preserved current plan." if (status_expected or current_unchanged) else "Failed activation changed current plan.")]


def strict_llm_rubric(llm, *, metric: str, rubric: dict, scope: str | None = None,
                      prompt_metric: str | None = None):
    """Create an external evaluator constrained to 0/0.5/1 and one evidence sentence.

    When judge_prompts.py has written criteria for (scope, metric), the judge
    receives them as plain text: a question, a definition and example for each
    score, rules, and the run's evidence formatted for reading. Metrics without
    written criteria fall back to the compact rubric dictionary.
    """
    from langchain_core.messages import HumanMessage, SystemMessage
    from pydantic import BaseModel, Field

    from backend.evaluation.judge_prompts import JUDGE_SYSTEM, render_prompt

    class Grade(BaseModel):
        score: float = Field(ge=0, le=1, description="Exactly 0.0, 0.5 or 1.0")
        evidence: str = Field(min_length=1, max_length=600,
                              description="One sentence naming what decided the score")

    judge = llm.with_structured_output(Grade)

    def evaluator(run, example):
        prediction = _value(run, "outputs", {}) or {}
        inputs = _value(run, "inputs", {}) or {}
        reference = _value(example, "outputs", {}) or {}
        written = render_prompt(scope, prompt_metric or metric, inputs, prediction, reference) if scope else None
        if written is not None:
            messages = [SystemMessage(content=JUDGE_SYSTEM), HumanMessage(content=written)]
        else:
            messages = [
                SystemMessage(content="You are a strict TELORA evaluator. Never infer facts absent from the evidence. "
                              "Score only 0, 0.5, or 1. Return one short evidence statement, no chain of thought."),
                HumanMessage(content=str({"metric": metric, "rubric": rubric, "inputs": inputs,
                                         "prediction": prediction, "reference": reference})),
            ]
        grade = judge.invoke(messages)
        score = float(grade.score)
        if score not in (0.0, 0.5, 1.0):
            score = 0.0
        return {"key": metric, "score": score, "comment": grade.evidence}
    return evaluator
