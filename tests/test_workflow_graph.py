"""LangGraph wiring: node plumbing and routing off already-persisted state only."""

import unittest
from unittest.mock import Mock, patch

from backend.schemas.request import RequestStatus as Status, WorkflowStage as Stage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.state import SharedWorkflowState
from backend.workflow.graph import build_workflow_graph


def shared_state(status, stage):
    return SharedWorkflowState(
        request_id="REQ-1", customer_id="C1", status=status, stage=stage,
        requirements=Requirements(NeedSelection("Data", "Medium"), None, None, 200))


class WorkflowGraphTests(unittest.TestCase):
    def test_graph_topology(self):
        graph = build_workflow_graph(request_service=Mock())
        self.assertEqual(set(graph.get_graph().nodes), {"__start__", "search_node", "design_node", "__end__"})

    def test_search_success_routes_to_and_calls_design_node(self):
        service = Mock()
        service.reconstruct_shared_state.return_value = shared_state(Status.PROCESSING, Stage.DESIGNING)
        with patch("backend.workflow.nodes.run_search_for_request") as search_call, \
             patch("backend.workflow.nodes.run_solution_design_for_request") as design_call:
            graph = build_workflow_graph(request_service=service)
            graph.invoke({"request_id": "REQ-1"})
        search_call.assert_called_once_with("REQ-1", request_service=service, llm=None, judge_llm=None)
        design_call.assert_called_once_with("REQ-1", request_service=service, llm=None)

    def test_needs_adjustment_and_error_end_without_calling_design_node(self):
        for status, stage in ((Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS), (Status.ERROR, Stage.ERROR)):
            with self.subTest(status=status):
                service = Mock()
                service.reconstruct_shared_state.return_value = shared_state(status, stage)
                with patch("backend.workflow.nodes.run_search_for_request") as search_call, \
                     patch("backend.workflow.nodes.run_solution_design_for_request") as design_call:
                    graph = build_workflow_graph(request_service=service)
                    graph.invoke({"request_id": "REQ-1"})
                search_call.assert_called_once()
                design_call.assert_not_called()

    def test_custom_llms_are_threaded_into_the_matching_node(self):
        service = Mock()
        service.reconstruct_shared_state.return_value = shared_state(Status.PROCESSING, Stage.DESIGNING)
        search_llm, judge_llm, design_llm = Mock(), Mock(), Mock()
        with patch("backend.workflow.nodes.run_search_for_request") as search_call, \
             patch("backend.workflow.nodes.run_solution_design_for_request") as design_call:
            graph = build_workflow_graph(request_service=service, search_llm=search_llm,
                                         judge_llm=judge_llm, design_llm=design_llm)
            graph.invoke({"request_id": "REQ-1"})
        search_call.assert_called_once_with("REQ-1", request_service=service, llm=search_llm, judge_llm=judge_llm)
        design_call.assert_called_once_with("REQ-1", request_service=service, llm=design_llm)


if __name__ == "__main__":
    unittest.main()
