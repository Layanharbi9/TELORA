"""Interactive terminal flow for creating, recommending, and applying a TELORA plan."""

from __future__ import annotations

import argparse
from contextlib import closing
from uuid import uuid4

from backend.services.executor import CLI_EXECUTOR_ID
from backend.services.request_service import RequestService
from backend.tools.apply_tool import apply_tool
from backend.workflow.graph import build_workflow_graph
from backend.services.tracing_service import traced_action

from backend.schemas.requirements import (
    NEEDS,
    NeedLevel,
    NeedSelection,
    RequirementSubmission,
    Requirements,
)


def _choose(prompt: str, options: tuple[str, ...], input_fn=input, output_fn=print) -> str:
    while True:
        output_fn(prompt)
        for index, option in enumerate(options, start=1):
            output_fn(f"{index}. {option}")
        choice = input_fn("Enter choice: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        for option in options:
            if choice.casefold() == option.casefold():
                return option
        output_fn("Invalid choice. Please try again.\n")


def _ask_need(label: str, excluded: set[str], input_fn=input, output_fn=print) -> NeedSelection:
    available = tuple(need for need in NEEDS if need not in excluded)
    need = _choose(f"Select your {label} need:\n", available, input_fn, output_fn)
    level = _choose(
        f"\nUsage level for {need}:\n",
        tuple(level.value for level in NeedLevel),
        input_fn,
        output_fn,
    )
    return NeedSelection(need=need, level=level)


def _ask_budget(input_fn=input, output_fn=print):
    while True:
        raw_budget = input_fn("\nPreferred Monthly Budget (SAR): ").strip()
        try:
            return Requirements.parse_budget(raw_budget)
        except ValueError as error:
            output_fn(f"Invalid budget: {error}. Please try again.")


def _ask_required_text(label: str, input_fn=input, output_fn=print) -> str:
    while True:
        value = input_fn(f"{label}: ").strip()
        if value:
            return value
        output_fn(f"{label} is required. Please try again.")


def collect_requirements(input_fn=input, output_fn=print) -> Requirements:
    output_fn("=" * 40)
    output_fn("TELORA - Personalized Telecom Plan")
    output_fn("=" * 40 + "\n")

    primary = _ask_need("primary", set(), input_fn, output_fn)
    selected = {primary.need}
    secondary = _ask_need("secondary", selected, input_fn, output_fn)
    selected.add(secondary.need)
    third = _ask_need("third", selected, input_fn, output_fn)
    budget = _ask_budget(input_fn, output_fn)
    return Requirements(primary, secondary, third, budget)


def collect_submission(input_fn=input, output_fn=print) -> RequirementSubmission:
    username = _ask_required_text("Username", input_fn, output_fn)
    customer_id = _ask_required_text("Customer ID", input_fn, output_fn)
    requirements = collect_requirements(input_fn, output_fn)
    return RequirementSubmission(username, customer_id, requirements)


def _choose_solution(solutions: list[dict], input_fn=input, output_fn=print) -> dict | None:
    if not solutions:
        return None
    output_fn("\nRecommended solutions:\n")
    for index, solution in enumerate(solutions, start=1):
        package = solution.get("package", {})
        addons = solution.get("addons", [])
        output_fn(f"{index}. Solution ID: {solution.get('solution_id', 'N/A')}")
        output_fn(f"   Package: {package.get('name', package.get('package_name', 'Package'))} "
                  f"({package.get('package_id', 'N/A')})")
        output_fn(f"   Data: {package.get('capabilities', {}).get('data_gb', 'N/A')} GB | "
                  f"Calls: {package.get('capabilities', {}).get('voice_minutes', 'N/A')} minutes | "
                  f"SMS: {package.get('capabilities', {}).get('sms', 'N/A')}")
        if addons:
            addon_details = ", ".join(
                f"{item.get('name', item.get('addon_name', 'Add-on'))} "
                f"({item.get('addon_id', 'N/A')})" for item in addons
            )
        else:
            addon_details = "None"
        output_fn(f"   Add-ons: {addon_details}")
        output_fn(f"   Total price: {solution.get('total_price_sar', 'N/A')} SAR/month")
        output_fn(f"   Budget: {solution.get('budget_status', 'N/A')}"
                  f"{' | Recommended' if solution.get('recommended') else ''}")
        needs_coverage = solution.get("needs_coverage", [])
        if needs_coverage:
            output_fn("   Needs coverage:")
            for coverage in needs_coverage:
                if isinstance(coverage, dict):
                    need = coverage.get("need", "Need")
                    status = coverage.get("coverage", "N/A")
                    evidence = coverage.get("evidence")
                    detail = f" — {evidence}" if evidence else ""
                    output_fn(f"      {need}: {status}{detail}")
                else:
                    output_fn(f"      {coverage}")
        if solution.get("trade_off"):
            output_fn(f"   Trade-off: {solution['trade_off']}")
        if solution.get("recommendation_reason"):
            output_fn(f"   Why: {solution['recommendation_reason']}")
    while True:
        choice = input_fn("\nChoose a solution number (or q to leave it for later): ").strip().lower()
        if choice in {"q", "quit"}:
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(solutions):
            return solutions[int(choice) - 1]
        output_fn("Enter one of the listed numbers, or q to quit.")


def continue_request(request_id: str, service: RequestService, input_fn=input, output_fn=print) -> dict | None:
    """Resume Search/Design if needed, then collect the customer's choice and apply it."""
    request = service.get_request(request_id)
    if request is None:
        raise ValueError(f"Request not found: {request_id}")

    if request.status.value == "PROCESSING" and request.stage.value == "SEARCHING":
        output_fn("\nSearching the catalog and preparing recommendations...\n")
        # Own the run so web startup reconciliation never fails it as abandoned.
        service.database.claim_execution(request_id, CLI_EXECUTOR_ID)
        build_workflow_graph(request_service=service).invoke({"request_id": request_id})
        request = service.get_request(request_id)

    if request is None:
        raise ValueError(f"Request not found after workflow: {request_id}")
    if request.status.value != "WAITING_FOR_USER" or request.stage.value != "SELECTION":
        checkpoint = service.get_checkpoint(request_id)
        solutions_data = checkpoint.final_solutions if checkpoint else None
        solutions = solutions_data.get("solutions", []) if isinstance(solutions_data, dict) else []
        if solutions:
            output_fn(f"Request {request_id} is at {request.status.value}/{request.stage.value}; "
                      "resume from its current workflow stage before selecting.")
        else:
            output_fn(f"No selectable solutions were produced. Request status: "
                      f"{request.status.value}/{request.stage.value}.")
        return None

    checkpoint = service.get_checkpoint(request_id)
    solutions_data = checkpoint.final_solutions if checkpoint else None
    solutions = solutions_data.get("solutions", []) if isinstance(solutions_data, dict) else []
    selected = _choose_solution(solutions, input_fn, output_fn)
    if selected is None:
        output_fn(f"Selection skipped. Resume later with: python -m backend.main --resume {request_id}")
        return None

    service.select_solution(request_id, selected)
    result = apply_tool.invoke({
        "customer_id": request.customer_id,
        "request_id": request_id,
        "selected_solution_id": selected["solution_id"],
        "package_ids": [selected["package"]["package_id"]],
        "addon_ids": [addon["addon_id"] for addon in selected.get("addons", [])],
    })
    output_fn("\nApplication result:")
    output_fn(f"Status: {result.get('status')}")
    if result.get("order_id"):
        output_fn(f"Order ID: {result['order_id']}")
    if result.get("total_price") is not None:
        output_fn(f"Current monthly price: {result['total_price']} SAR")
    if result.get("error_message"):
        output_fn(f"Error: {result['error_message']}")
    return result


def _run_traced_workflow(request_id: str, customer_id: str, service: RequestService,
                        action, *, requirements: Requirements | None = None) -> dict:
    """Root trace spans request persistence, graph, user selection, Apply, and final DB read."""
    from time import perf_counter
    from langsmith.run_helpers import get_current_run_tree

    started = perf_counter()
    root_run = get_current_run_tree()
    if root_run is not None and service.get_request(request_id) is not None:
        service.database.record_workflow_trace_root(request_id, str(root_run.id), request_id)
    result = action()
    root_run = get_current_run_tree()
    if root_run is not None:
        service.database.record_workflow_trace_root(request_id, str(root_run.id), request_id)
    try:
        request = service.get_request(request_id)
        checkpoint = service.get_checkpoint(request_id)
    except Exception:
        request = checkpoint = None
    order_id = result.get("order_id") if isinstance(result, dict) else None
    try:
        with closing(service.database._connect()) as connection:
            business = connection.execute(
                "SELECT business_status, order_id FROM requests WHERE request_id=?", (request_id,),
            ).fetchone()
            order = connection.execute(
                "SELECT status, updated_at FROM orders WHERE order_id=?", (order_id,),
            ).fetchone() if order_id else None
        from backend.services.current_plan_service import CurrentPlanService
        current_plan = CurrentPlanService(service.database).get_current_plan(customer_id)
    except Exception:
        business = order = current_plan = None
    return {
        "request_id": request_id,
        "customer_id": customer_id,
        "final_status": request.status.value if request else "MISSING",
        "final_stage": request.stage.value if request else "MISSING",
        "business_status": business["business_status"] if business else None,
        "selected_solution_id": ((checkpoint.selected_solution or {}).get("solution_id")
                                  if checkpoint else None),
        "request_order_id": business["order_id"] if business else None,
        "order_id": order_id,
        "order_status": order["status"] if order else None,
        "order_updated_at": order["updated_at"] if order else None,
        "current_plan": current_plan,
        "apply_status": result.get("status") if isinstance(result, dict) else None,
        "apply_result": result,
        "total_latency_ms": round((perf_counter() - started) * 1000, 3),
        "requirements": requirements.to_dict() if requirements else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", metavar="REQUEST_ID", help="Continue an existing TELORA request")
    args = parser.parse_args()

    service = RequestService()
    if args.resume:
        request = service.get_request(args.resume)
        if request is None:
            raise ValueError(f"Request not found: {args.resume}")
        traced_action(
            "telora.workflow", lambda: _run_traced_workflow(
                args.resume, request.customer_id, service,
                lambda: continue_request(args.resume, service), requirements=request.requirements,
            ),
            request_id=args.resume, customer_id=request.customer_id,
            workflow_id=args.resume, metadata={"workflow_version": "1", "resume": True},
            tags=("workflow",),
            parent_run_id=(service.database.get_workflow_trace_root(args.resume)),
        )
        return

    submission = collect_submission()
    request_id = f"REQ-{uuid4().hex.upper()}"

    def start_and_continue():
        request = service.start_customer_request(
            submission.customer_id, submission.requirements, request_id=request_id,
            executor_id=CLI_EXECUTOR_ID,
        )
        print(f"\nRequest created: {request.request_id}")
        return continue_request(request.request_id, service)

    traced_action(
        "telora.workflow", lambda: _run_traced_workflow(
            request_id, submission.customer_id, service, start_and_continue,
            requirements=submission.requirements,
        ),
        request_id=request_id, customer_id=submission.customer_id,
        workflow_id=request_id, metadata={"workflow_version": "1", "resume": False},
        tags=("workflow",),
    )


if __name__ == "__main__":
    main()
