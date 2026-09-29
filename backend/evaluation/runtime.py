"""Low-cost deterministic evaluations attached to live TELORA agent traces."""

from __future__ import annotations

from contextlib import closing

from backend.services.current_plan_service import CurrentPlanService
from backend.services.tracing_service import record_evaluation
from backend.schemas.search import search_needs_from_requirements


def _metric(key: str, passed: bool, evidence: str, *, score: float | None = None,
            applicable: bool = True) -> dict:
    if not applicable:
        return {"key": key, "score": 1.0, "value": "NOT_APPLICABLE", "comment": evidence}
    value = (1.0 if passed else 0.0) if score is None else max(0.0, min(1.0, float(score)))
    return {"key": key, "score": value,
            "value": "PASS" if value == 1 else ("PARTIAL" if value > 0 else "FAIL"),
            "comment": evidence}


def evaluate_search_runtime(state, database, *, request_id: str, agent_run_id: str | None,
                           session_id: str | None = None, parent_run=None) -> dict:
    pool = state.candidate_pool
    trace = state.search_trace
    packages = pool.package_candidates if pool else []
    addons = pool.addon_candidates if pool else []
    stages = trace.stages if trace else []
    by_stage = {stage.stage: stage for stage in stages}
    retrieved = by_stage.get("RETRIEVE")
    retrieved_package_ids = set(retrieved.details.get("package_ids", [])) if retrieved else set()
    retrieved_addon_ids = set(retrieved.details.get("addon_ids", [])) if retrieved else set()
    final_package_ids = [row.package_id for row in packages]
    final_addon_ids = [row.addon_id for row in addons]
    judge = by_stage.get("JUDGE")
    judge_details = judge.details if judge else {}
    verdict, gaps = judge_details.get("verdict"), judge_details.get("gaps", [])
    requirements = state.user_requirements
    requested_needs = [requirements.primary_need]
    requested_needs.extend(item for item in (requirements.secondary_need, requirements.third_need) if item)
    requested_names = {item.need for item in requested_needs}
    matched_names = {
        item.need for candidate in [*packages, *addons] for item in candidate.matched_needs
    }
    coverage = (len(requested_names & matched_names) / len(requested_names)) if requested_names else 1.0
    candidate_ids_valid = bool(pool) and (
        len(final_package_ids) == len(set(final_package_ids))
        and len(final_addon_ids) == len(set(final_addon_ids))
    )
    retrieval_correct = bool(retrieved) and set(final_package_ids) <= retrieved_package_ids \
        and set(final_addon_ids) <= retrieved_addon_ids
    reassess_count = sum(stage.stage == "REASSESS" for stage in stages)
    retrieval_count = sum(stage.stage == "RETRIEVE" for stage in stages)
    reassessment_ok = retrieval_count == 1 and reassess_count <= 1 \
        and reassess_count == (1 if verdict == "INSUFFICIENT" else 0)
    judge_ok = bool(judge) and (
        (verdict == "SUFFICIENT" and not gaps) or
        (verdict == "INSUFFICIENT" and bool(gaps)
         and all(gap.get("need_priority") in {need.priority for need in requested_needs}
                 for gap in gaps))
    )
    # The PLAN tool priorities are also contract-checked against the actual
    # request priorities; this catches an invalid handoff even if later stages ran.
    request_priorities = {item.priority for item in search_needs_from_requirements(requirements)}
    plan_ok = bool(trace and set(priority for task in trace.search_plan.tasks
                                 for priority in task.need_priorities) == request_priorities)
    with closing(database._connect()) as connection:
        package_rows = {row["package_id"]: dict(row) for row in
                        connection.execute("SELECT * FROM packages").fetchall()}
        addon_rows = {row["addon_id"]: dict(row) for row in
                      connection.execute("SELECT * FROM addons").fetchall()}
    canonical_ok = all(
        row.package_id in package_rows
        and row.name == package_rows[row.package_id]["package_name"]
        and float(row.price_sar) == float(package_rows[row.package_id]["monthly_price_sar"])
        for row in packages
    ) and all(
        row.addon_id in addon_rows
        and row.name == addon_rows[row.addon_id]["addon_name"]
        and float(row.price_sar) == float(addon_rows[row.addon_id]["monthly_price_sar"])
        for row in addons
    )
    metrics = [
        _metric("plan_contract", plan_ok, "Search plan covers exactly the requested need priorities." if plan_ok else "Search plan priorities differ from the request."),
        _metric("retrieval_correctness", retrieval_correct, "Final candidates come from the recorded retrieval pool." if retrieval_correct else "Final pool has candidates absent from recorded retrieval."),
        _metric("candidate_id_validity", candidate_ids_valid, "Final IDs are unique and correctly separated by catalog type." if candidate_ids_valid else "Candidate IDs are missing or duplicated."),
        _metric("canonical_integrity", canonical_ok, "Candidate names and prices match the SQLite catalog." if canonical_ok else "A candidate name or price differs from SQLite."),
        _metric("needs_coverage", bool(requested_names) and coverage == 1.0,
                f"Retrieved candidates match {len(requested_names & matched_names)}/{len(requested_names)} requested needs.", score=coverage),
        _metric("runtime_judge_contract", judge_ok, "Judge verdict, gaps, and need priorities are structurally consistent." if judge_ok else "Judge verdict or gaps conflict with the requested needs."),
        _metric("reassessment_correctness", reassessment_ok, "There is one retrieval and at most one reassessment consistent with the Judge." if reassessment_ok else "Retrieval/reassessment counts conflict with the Judge verdict."),
        _metric("grounded_catalog_data", canonical_ok, "Structured catalog facts match SQLite." if canonical_ok else "Unsupported structured catalog facts were detected."),
    ]
    return record_evaluation("search-agent", metrics, request_id=request_id,
                             agent_run_id=agent_run_id,
                             session_id=session_id, parent_run=parent_run,
                             inputs={"request_id": request_id,
                                     "requested_needs": sorted(requested_names),
                                     "candidate_count": len(packages) + len(addons)})


def evaluate_solution_runtime(requirements, candidate_pool, final_solutions: dict,
                              database, *, request_id: str,
                              agent_run_id: str | None, session_id: str | None = None,
                              parent_run=None) -> dict:
    solutions = final_solutions.get("solutions", [])
    candidate_package_ids = {item.package_id for item in candidate_pool.package_candidates}
    candidate_addon_ids = {item.addon_id for item in candidate_pool.addon_candidates}
    complete = bool(solutions) and all(
        item.get("solution_id") and item.get("package", {}).get("package_id")
        and "needs_coverage" in item and "trade_off" in item and "budget_status" in item
        and "recommended" in item for item in solutions
    )
    member = bool(solutions) and all(
        item.get("package", {}).get("package_id") in candidate_package_ids
        and {addon.get("addon_id") for addon in item.get("addons", [])} <= candidate_addon_ids
        for item in solutions
    )
    budget = requirements.preferred_budget_sar
    budget_ok, price_ok, composition_ok = True, True, True
    covered, requested = set(), set()
    for requirement in (requirements.primary_need, requirements.secondary_need, requirements.third_need):
        if requirement:
            requested.add(requirement.need)
    for solution in solutions:
        package_id = solution.get("package", {}).get("package_id")
        addon_ids = [item.get("addon_id") for item in solution.get("addons", [])]
        try:
            with closing(database._connect()) as connection:
                package, addons, total = CurrentPlanService.catalog_composition(
                    connection, [package_id], addon_ids)
            price_ok &= float(solution.get("total_price_sar")) == float(total)
            composition_ok &= package["package_id"] in candidate_package_ids \
                and {item["addon_id"] for item in addons} <= candidate_addon_ids
        except (TypeError, ValueError, KeyError):
            price_ok = composition_ok = False
        total_value = solution.get("total_price_sar")
        status = str(solution.get("budget_status", ""))
        budget_ok &= ((total_value <= budget and status.startswith("WITHIN_BUDGET"))
                      or (total_value > budget and status.startswith("ABOVE_BUDGET")))
        coverage_rows = solution.get("needs_coverage", [])
        covered.update(item.get("need") for item in coverage_rows if item.get("coverage") == "COVERED")
    coverage_score = (len(covered & requested) / len(requested)) if requested else 1.0
    metrics = [
        _metric("solution_completeness", complete, "Solutions contain the required display and decision fields." if complete else "A solution or required field is missing."),
        _metric("candidate_constraints", member and composition_ok, "Solutions use only valid, grounded candidates from Search." if member and composition_ok else "A solution violates candidate membership or catalog composition."),
        _metric("budget_compliance", budget_ok, "Budget labels match each canonical price and customer budget." if budget_ok else "A budget label conflicts with price or budget."),
        _metric("canonical_price", price_ok, "Each solution total equals its SQLite package/add-on price." if price_ok else "A solution price differs from the canonical catalog total."),
        _metric("needs_coverage", coverage_score == 1.0, f"Solutions mark {len(covered & requested)}/{len(requested)} requested needs covered.", score=coverage_score),
    ]
    return record_evaluation("solution-design", metrics, request_id=request_id,
                             agent_run_id=agent_run_id,
                             session_id=session_id, parent_run=parent_run,
                             inputs={"request_id": request_id,
                                     "candidate_package_count": len(candidate_package_ids),
                                     "candidate_addon_count": len(candidate_addon_ids)})


def evaluate_apply_runtime(result: dict, database, *, request_id: str,
                           agent_run_id: str | None, session_id: str | None = None,
                           parent_run=None) -> dict:
    order_id = result.get("order_id")
    customer_id = result.get("customer_id")
    status = result.get("status")
    successful = status == "ACTIVE" and result.get("success") is True
    failed = status == "FAILED" and result.get("success") is False
    with closing(database._connect()) as connection:
        current_row = connection.execute("SELECT * FROM current_plans WHERE customer_id=?", (customer_id,)).fetchone()
        request = connection.execute("SELECT business_status,order_id FROM requests WHERE request_id=?",
                                     (request_id,)).fetchone()
        order = connection.execute("SELECT status FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
    current = CurrentPlanService(database).get_current_plan(customer_id) if current_row else None
    new_plan = result.get("new_plan") or {}
    old_plan = result.get("previous_plan") or {}
    state_consistent = bool(request) and (
        successful and request["business_status"] == "ACTIVE" and request["order_id"] == order_id
        and order and order["status"] == "ACTIVE" and current
        and [current["package_id"]] == new_plan.get("package_ids", [])
        and current["addon_ids"] == new_plan.get("addon_ids", [])
        and current["total_price"] == new_plan.get("total_price")
        or failed and request["business_status"] == "FAILED"
        and (not order_id or (order and order["status"] == "FAILED"))
        and current and [current["package_id"]] == old_plan.get("package_ids", [])
        and current["addon_ids"] == old_plan.get("addon_ids", [])
        and current["total_price"] == old_plan.get("total_price")
    )
    validation_recorded = successful or (failed and result.get("failure_recorded") is True)
    price_applicable = bool(result.get("package_ids")) and result.get("total_price") is not None
    canonical_price_ok = False
    if price_applicable:
        try:
            with closing(database._connect()) as connection:
                _, _, catalog_total = CurrentPlanService.catalog_composition(
                    connection, result.get("package_ids", []), result.get("addon_ids", []))
            canonical_price_ok = float(catalog_total) == float(result["total_price"])
        except (TypeError, ValueError, KeyError):
            canonical_price_ok = False
    metrics = [
        _metric("input_and_selection_validation", validation_recorded, "Apply validation produced a consistent terminal result." if validation_recorded else "Validation did not produce a recorded success/failure."),
        _metric("canonical_price", canonical_price_ok, "Apply total equals the SQLite catalog composition." if canonical_price_ok else "Apply result price differs from the SQLite catalog composition.", applicable=price_applicable),
        _metric("request_order_plan_consistency", bool(state_consistent), "Request, order, and Current Plan agree with the Apply result." if state_consistent else "Request, order, or Current Plan disagrees with the Apply result."),
        _metric("failure_rollback", not failed or bool(state_consistent), "Failed application preserved the previous Current Plan." if (not failed or state_consistent) else "Failed application changed the Current Plan or left history inconsistent."),
    ]
    return record_evaluation("apply-tool", metrics, request_id=request_id,
                             agent_run_id=agent_run_id,
                             session_id=session_id, parent_run=parent_run,
                             inputs={"request_id": request_id, "order_id": order_id,
                                     "status": status})
