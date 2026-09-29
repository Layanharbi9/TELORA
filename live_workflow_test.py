"""Manual, real end-to-end live test of the integrated LangGraph workflow.

Creates one fresh request at PROCESSING/SEARCHING against the real SQLite
database and invokes the compiled LangGraph graph exactly once, driving it
through Search -> Solution Design entirely via build_workflow_graph() (never
calling either agent, or their workflow wrappers, directly). Uses the real
configured OpenAI API. Run explicitly; never imported to execute.

This permanently persists the created request/checkpoint to the real database.
"""
import json
from uuid import uuid4

from backend.schemas.requirements import NeedSelection, Requirements
from backend.services.request_service import RequestService
from backend.workflow.graph import build_workflow_graph


def encode(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return repr(value)


def show(title, value):
    print("\n=== " + title + " ===", flush=True)
    print(json.dumps(value, default=encode, indent=2), flush=True)


def main() -> None:
    service = RequestService()
    request_id = f"REQ-LIVE-{uuid4().hex.upper()}"
    customer_id = "LIVE-WORKFLOW-TEST"
    requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 220)

    request = service.create_request(request_id, customer_id, requirements)
    print(f"Created request {request.request_id} for customer {request.customer_id} "
         f"at {request.status.value}/{request.stage.value}", flush=True)

    # Execution goes entirely through the compiled graph; no agent or workflow
    # wrapper is called directly from this script.
    graph = build_workflow_graph(request_service=service)
    graph.invoke({"request_id": request_id})

    final_request = service.get_request(request_id)
    checkpoint = service.get_checkpoint(request_id)

    show("REQUEST_ID", final_request.request_id)
    show("CUSTOMER_ID", final_request.customer_id)
    show("FINAL STATUS", final_request.status.value)
    show("FINAL STAGE", final_request.stage.value)
    show("CANDIDATE_POOL", checkpoint.candidate_pool)
    show("CANDIDATE_SOLUTIONS", checkpoint.candidate_solutions)
    show("SOLUTION_VALIDATION", checkpoint.solution_validation)
    show("FINAL_SOLUTIONS", checkpoint.final_solutions)


if __name__ == "__main__":
    main()
