"""Terminal entry point for Search on an existing persisted Request."""

import argparse
import json

from backend.services.executor import CLI_EXECUTOR_ID
from backend.services.request_service import RequestService
from backend.services.search_workflow import run_search_for_request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id", help="Existing PROCESSING/SEARCHING Request ID")
    args = parser.parse_args()
    # Own the run so web startup reconciliation never fails it as abandoned.
    RequestService().database.claim_execution(args.request_id, CLI_EXECUTOR_ID)
    result = run_search_for_request(args.request_id)
    print(json.dumps({"state": result.state.model_dump(mode="json"),
                      "search_trace": result.search_trace.to_dict() if result.search_trace else None}, indent=2))


if __name__ == "__main__":
    main()
