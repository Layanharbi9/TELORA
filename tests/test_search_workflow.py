"""Request/Search integration without live model or catalog calls."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage

from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase
from backend.database.migrations import SCHEMA_VERSION
from backend.schemas.request import RequestStatus as Status, WorkflowStage as Stage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import (AssessmentSelection, CandidatePool, PackageCandidate,
                                    PackageSearchOutput, SearchJudgeResult, SearchMetadata,
                                    SearchNeed, SearchStatus)
from backend.schemas.state import SearchTrace, TELORAState
from backend.services.request_service import InvalidLifecycleTransition, RequestService
from backend.services.search_workflow import run_search_for_request


class SearchWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.sqlite3"
        # Exercise against a copy including existing catalog and historical rows.
        with closing(sqlite3.connect(DEFAULT_DATABASE_PATH.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:
                source.backup(target)
        self.database = SearchDatabase(self.path)
        self.service = RequestService(self.database)
        self.requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 220)
        self.service.create_request("REQ-INTEGRATION", "CUST-01", self.requirements)
        self.pool = CandidatePool(package_candidates=[PackageCandidate(
            package_id="PKG-1", name="Canonical", price_sar=200,
            matched_needs=[SearchNeed(need="Data", level="Medium", priority=1)],
            match_reasons=["Data available"], capabilities={"data_gb": 100})],
            addon_candidates=[], search_status=SearchStatus.SUCCESS)
        self.trace = SearchTrace("llm_search_agent", [])
        self.protected = self.snapshot_protected()
        self.addCleanup(self.assert_protected)

    def snapshot_protected(self):
        with closing(self.database._connect()) as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                    for table in ("packages", "addons", "customers")}

    def assert_protected(self):
        self.assertEqual(self.snapshot_protected(), self.protected)
        with closing(self.database._connect()) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def fake_result(self, state, **kwargs):
        return TELORAState(state.request_id, state.user_requirements, self.pool, self.trace)

    def run_search(self):
        return run_search_for_request("REQ-INTEGRATION", request_service=self.service)

    def test_persisted_requirements_success_and_trace_separation(self):
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result) as agent, \
             patch.object(self.service, "reconstruct_shared_state", wraps=self.service.reconstruct_shared_state) as reconstruct, \
             patch.object(self.database, "get_customers", side_effect=AssertionError("No customer lookup")):
            result = self.run_search()
        agent.assert_called_once()
        state = agent.call_args.args[0]
        self.assertEqual(state.user_requirements, self.requirements)
        self.assertFalse(hasattr(state, "customer_id"))
        self.assertEqual(reconstruct.call_count, 2)
        self.assertEqual((result.state.status, result.state.stage), (Status.PROCESSING, Stage.DESIGNING))
        self.assertEqual(result.state, self.service.reconstruct_shared_state("REQ-INTEGRATION"))
        self.assertEqual(result.state.candidate_pool, self.pool)
        self.assertIs(result.search_trace, self.trace)
        self.assertFalse(hasattr(result, "candidate_pool"))
        self.assertNotIn("search_trace", result.state.model_dump())
        with closing(self.database._connect()) as connection:
            for table in ("requests", "workflow_checkpoints"):
                self.assertFalse(any("trace" in row[1] for row in connection.execute(f"PRAGMA table_info({table})")))

    def test_search_phase_is_null_until_the_checking_boundary(self):
        self.assertIsNone(self.database.get_search_phase("REQ-INTEGRATION"))
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result) as agent:
            self.run_search()
        # The workflow hands Search a progress callback; a Search that never
        # reaches the boundary (this fake) leaves the phase NULL.
        self.assertTrue(callable(agent.call_args.kwargs["on_phase"]))
        self.assertIsNone(self.database.get_search_phase("REQ-INTEGRATION"))

    def test_a_new_search_run_resets_a_stale_phase(self):
        self.database.set_search_phase("REQ-INTEGRATION", "CHECKING")
        self.assertEqual(self.database.get_search_phase("REQ-INTEGRATION"), "CHECKING")
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result):
            self.run_search()
        self.assertIsNone(self.database.get_search_phase("REQ-INTEGRATION"))

    def test_phase_is_only_written_while_searching(self):
        self.database.update_request("REQ-INTEGRATION", status=Status.PROCESSING, stage=Stage.DESIGNING)
        self.database.set_search_phase("REQ-INTEGRATION", "CHECKING")
        self.assertIsNone(self.database.get_search_phase("REQ-INTEGRATION"))

    def test_phase_write_failure_never_changes_the_search_outcome(self):
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result), \
             patch.object(self.database, "set_search_phase", side_effect=sqlite3.OperationalError("locked")), \
             self.assertLogs("backend.services.search_workflow", level="WARNING"):
            result = self.run_search()
        self.assertEqual((result.state.status, result.state.stage), (Status.PROCESSING, Stage.DESIGNING))

    def test_semantic_statuses_route_to_adjustment(self):
        for status in (SearchStatus.INSUFFICIENT, SearchStatus.NO_RESULTS):
            with self.subTest(status=status):
                self.database.update_request("REQ-INTEGRATION", status=Status.PROCESSING, stage=Stage.SEARCHING)
                self.pool = self.pool.model_copy(update={"search_status": status})
                with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result):
                    result = self.run_search()
                self.assertEqual((result.state.status, result.state.stage), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS))
                self.assertEqual(result.state.candidate_pool, self.pool)

    def test_completed_error_preserves_previous_checkpoint(self):
        previous = self.pool
        self.database.update_checkpoint("REQ-INTEGRATION", candidate_pool=previous)
        self.pool = self.pool.model_copy(update={"search_status": SearchStatus.ERROR})
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result):
            result = self.run_search()
        self.assertEqual((result.state.status, result.state.stage), (Status.ERROR, Stage.ERROR))
        self.assertEqual(result.state.candidate_pool, previous)
        self.assertEqual(result.search_trace, self.trace)

    def test_execution_exceptions_mark_error_and_reraise(self):
        for error in (RuntimeError("unexpected"), PermissionError("authentication"), sqlite3.OperationalError("database")):
            self.database.update_request("REQ-INTEGRATION", status=Status.PROCESSING, stage=Stage.SEARCHING)
            self.database.update_checkpoint("REQ-INTEGRATION", candidate_pool=self.pool)
            with patch("backend.services.search_workflow.run_search_agent", side_effect=error):
                with self.assertRaises(type(error)) as caught:
                    self.run_search()
            self.assertIs(caught.exception, error)
            state = self.service.reconstruct_shared_state("REQ-INTEGRATION")
            self.assertEqual((state.status, state.stage), (Status.ERROR, Stage.ERROR))
            self.assertEqual(state.candidate_pool, self.pool)

    def test_invalid_states_and_repeat_invocation_do_not_call_search(self):
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result) as agent:
            self.run_search()
            for status, stage in ((Status.PROCESSING, Stage.DESIGNING), (Status.PROCESSING, Stage.VALIDATING),
                                  (Status.PROCESSING, Stage.PREPARING_SOLUTIONS), (Status.WAITING_FOR_USER, Stage.SELECTION),
                                  (Status.PROCESSING, Stage.APPLYING), (Status.COMPLETED, Stage.COMPLETED),
                                  (Status.ERROR, Stage.ERROR), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS)):
                self.database.update_request("REQ-INTEGRATION", status=status, stage=stage)
                before = self.path.read_bytes()
                with self.assertRaises(InvalidLifecycleTransition):
                    self.run_search()
                self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(agent.call_count, 1)

    def test_resubmission_uses_same_id_and_updated_requirements(self):
        self.pool = self.pool.model_copy(update={"search_status": SearchStatus.INSUFFICIENT})
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result) as agent:
            self.run_search()
            requirements = Requirements(NeedSelection("Calls", "High"), None, None, 300)
            self.service.resubmit_requirements("REQ-INTEGRATION", requirements)
            self.assertIsNone(self.service.get_checkpoint("REQ-INTEGRATION").candidate_pool)
            self.pool = self.pool.model_copy(update={"search_status": SearchStatus.SUCCESS})
            result = self.run_search()
        self.assertEqual(agent.call_args.args[0].user_requirements, requirements)
        self.assertEqual(result.state.request_id, "REQ-INTEGRATION")
        self.assertEqual(result.state.stage, Stage.DESIGNING)

    def test_persistence_failure_rolls_back_output_and_marks_error(self):
        with closing(self.database._connect()) as connection:
            connection.execute("""CREATE TRIGGER fail_designing BEFORE UPDATE OF stage ON requests
                                  WHEN NEW.stage = 'DESIGNING'
                                  BEGIN SELECT RAISE(ABORT, 'forced failure'); END""")
        with patch("backend.services.search_workflow.run_search_agent", side_effect=self.fake_result):
            with self.assertRaises(sqlite3.IntegrityError):
                self.run_search()
        state = self.service.reconstruct_shared_state("REQ-INTEGRATION")
        self.assertEqual(state.status, Status.ERROR)
        self.assertIsNone(state.candidate_pool)

    def test_error_write_failure_preserves_original_exception(self):
        error = RuntimeError("search failed")
        with patch("backend.services.search_workflow.run_search_agent", side_effect=error), \
             patch.object(self.service, "mark_error", side_effect=sqlite3.OperationalError("unavailable")):
            with self.assertRaises(RuntimeError) as caught:
                self.run_search()
        self.assertIs(caught.exception, error)
        self.assertTrue(any("Could not persist ERROR" in note for note in error.__notes__))

    def test_real_agent_with_fake_models_returns_trace_and_persists_through_service(self):
        model, assessor, judge = (Mock() for _ in range(3))
        # The model natively decides to call package_search itself, then finishes.
        tool_model = Mock()
        tool_model.invoke.side_effect = [
            AIMessage(content="", tool_calls=[{"name": "package_search", "args": {
                "needs": [{"need": "Data", "level": "Medium", "priority": 1}],
                "preferred_budget_sar": 220}, "id": "call-1", "type": "tool_call"}]),
            AIMessage(content="Data suitability", tool_calls=[]),
        ]
        model.bind_tools.return_value = tool_model
        assessor.invoke.return_value = AssessmentSelection(package_ids=["PKG-1"], addon_ids=[], search_status="SUCCESS")
        judge.invoke.return_value = SearchJudgeResult(verdict="SUFFICIENT", gaps=[], evaluation_summary="Sufficient")
        model.with_structured_output.side_effect = lambda schema, **kwargs: {
            AssessmentSelection: assessor, SearchJudgeResult: judge}[schema]
        tool = Mock()
        tool.invoke.return_value = PackageSearchOutput(status="SUCCESS", candidates=self.pool.package_candidates,
            search_metadata=SearchMetadata(tool_name="package_search", query_summary="Data", records_considered=1, records_returned=1))
        with patch("backend.agents.search_agent.SEARCH_TOOL_MAP", {"package_search": tool}):
            result = run_search_for_request("REQ-INTEGRATION", request_service=self.service, llm=model, judge_llm=model)
        self.assertEqual(result.state.stage, Stage.DESIGNING)
        # The real pipeline also preserves the assessor's own claimed status,
        # advisory only, alongside the code-computed routing search_status.
        expected_pool = self.pool.model_copy(update={"assessment_status": SearchStatus.SUCCESS})
        self.assertEqual(result.state.candidate_pool, expected_pool)
        self.assertEqual([stage.stage for stage in result.search_trace.stages],
                         ["PLAN", "RETRIEVE", "REDUCE", "ASSESS", "LOOKUP", "JUDGE", "OUTPUT", "ROUTE"])
        tool.invoke.assert_called_once()
        # The real agent reached the FINDING -> CHECKING boundary while SEARCHING.
        self.assertEqual(self.database.get_search_phase("REQ-INTEGRATION"), "CHECKING")


if __name__ == "__main__":
    unittest.main()
