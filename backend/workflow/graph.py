"""LangGraph orchestration: START -> search_node -> [conditional] -> design_node -> END.

Graph state carries only request_id. RequestService/SQLite remains the sole
source of truth; nodes persist through the existing workflow service wrappers
and routing re-reads persisted state -- nothing here duplicates
SharedWorkflowState, agent logic, or tracing.

Customer selection is an external user-interaction boundary. This graph ends
when design_node persists WAITING_FOR_USER/SELECTION; the application then calls
RequestService.select_solution and the deterministic Apply service. Apply writes
the existing request/checkpoint lifecycle back to SQLite without an LLM call or
a duplicate graph state.
"""
from typing import TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from backend.services.request_service import RequestService
from backend.workflow.nodes import make_design_node, make_search_node
from backend.workflow.routing import make_route_after_search


class WorkflowGraphState(TypedDict):
    request_id: str


def build_workflow_graph(
    *, request_service: RequestService | None = None,
    search_llm: ChatOpenAI | None = None, judge_llm: ChatOpenAI | None = None,
    design_llm: ChatOpenAI | None = None,
) -> CompiledStateGraph:
    """Compile the Search -> Solution Design graph; each node calls an existing, unmodified wrapper."""
    service = request_service if request_service is not None else RequestService()
    graph = StateGraph(WorkflowGraphState)
    graph.add_node("search_node", make_search_node(service, llm=search_llm, judge_llm=judge_llm))
    graph.add_node("design_node", make_design_node(service, llm=design_llm))
    graph.add_edge(START, "search_node")
    graph.add_conditional_edges("search_node", make_route_after_search(service))
    graph.add_edge("design_node", END)
    return graph.compile()
