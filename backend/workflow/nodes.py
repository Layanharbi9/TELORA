"""Thin LangGraph nodes wrapping the existing, unmodified workflow service functions.

Each node only calls an existing service wrapper (run_search_for_request /
run_solution_design_for_request) and returns no state update: request_id is the
only thing carried in graph state, and RequestService/SQLite remains the sole
source of truth for everything else. No agent or tracing logic is duplicated here.
"""
from typing import Any, Mapping

from langchain_openai import ChatOpenAI

from backend.services.request_service import RequestService
from backend.services.search_workflow import run_search_for_request
from backend.services.solution_design_workflow import run_solution_design_for_request


def make_search_node(request_service: RequestService, llm: ChatOpenAI | None = None,
                     judge_llm: ChatOpenAI | None = None):
    def search_node(state: Mapping[str, Any]) -> dict:
        run_search_for_request(state["request_id"], request_service=request_service, llm=llm, judge_llm=judge_llm)
        return {}
    return search_node


def make_design_node(request_service: RequestService, llm: ChatOpenAI | None = None):
    def design_node(state: Mapping[str, Any]) -> dict:
        run_solution_design_for_request(state["request_id"], request_service=request_service, llm=llm)
        return {}
    return design_node
