"""Create and run explicit LangSmith evaluation datasets/experiments."""

from __future__ import annotations

import argparse
import os
import sys

from langsmith import Client

from backend.evaluation.datasets import sync_langsmith_datasets
from backend.evaluation.evaluators import build_evaluators
from backend.evaluation.targets import (
    apply_target, end_to_end_target, search_target, solution_design_target, workflow_target,
)
from backend.services.tracing_service import project_name


TARGETS = {
    "search_agent_eval": search_target,
    "solution_design_eval": solution_design_target,
    "apply_tool_eval": apply_target,
    "workflow_eval": workflow_target,
    "end_to_end_eval": end_to_end_target,
}


def _client() -> Client:
    if not os.getenv("LANGSMITH_API_KEY"):
        raise RuntimeError("Set LANGSMITH_API_KEY to create datasets or run LangSmith experiments.")
    return Client(api_key=os.environ["LANGSMITH_API_KEY"],
                  api_url=os.getenv("LANGSMITH_ENDPOINT") or None)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync-datasets", help="Create missing catalog-derived TELORA datasets")
    sync.add_argument("--database", default=None)
    run = commands.add_parser("run", help="Run one explicitly selected LangSmith experiment")
    run.add_argument("--dataset", choices=sorted(TARGETS), required=True)
    run.add_argument("--semantic", action="store_true",
                     help="Add strict LLM-as-a-judge evaluators (additional model/API cost)")
    run.add_argument("--max-concurrency", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        client = _client()
        if args.command == "sync-datasets":
            result = sync_langsmith_datasets(client, args.database) if args.database else sync_langsmith_datasets(client)
            for dataset, count in result.items():
                print(f"{dataset}: {count} new examples")
            return 0
        llm = None
        if args.semantic:
            from langchain_openai import ChatOpenAI
            llm = ChatOpenAI(model=os.getenv("EVALUATION_MODEL", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
                             temperature=0, max_retries=0)
        result = client.evaluate(
            TARGETS[args.dataset], data=args.dataset,
            evaluators=build_evaluators(args.dataset, llm=llm),
            metadata={"application": "TELORA", "dataset": args.dataset,
                      "environment": os.getenv("TELORA_ENVIRONMENT", "development")},
            experiment_prefix=f"telora-{args.dataset}",
            description=f"TELORA evaluation for {args.dataset}; labels come from SQLite/business rules.",
            max_concurrency=max(1, args.max_concurrency),
        )
        print(result)
        return 0
    except Exception as error:
        print(f"Evaluation command failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
