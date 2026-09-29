"""Request/Solution Design integration: Option B, one atomic transition, no live model calls."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from backend.agents import solution_design_agent as a
from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase
from backend.schemas.request import RequestStatus as Status, WorkflowStage as Stage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import AddonCandidate, CandidatePool, PackageCandidate
from backend.schemas.solution import SolutionComparison, SolutionDesignPlan, SolutionRecommendation
from backend.services.request_service import InvalidLifecycleTransition, RequestService
from backend.services.solution_design_workflow import run_solution_design_for_request
from tests.test_final_solution_output import candidate_solution, guardrail


class SolutionDesignWorkflowTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.sqlite3"
        with closing(sqlite3.connect(DEFAULT_DATABASE_PATH.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(self.path)) as target:
                source.backup(target)
        self.database = SearchDatabase(self.path)
        self.service = RequestService(self.database)
        self.requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 200)
        self.service.create_request("REQ-INTEGRATION", "CUST-01", self.requirements)
        self.pool = CandidatePool(search_status="SUCCESS", addon_candidates=[
            AddonCandidate(addon_id="A1", name="Roaming Pack", price_sar=20,
                           matched_needs=[], match_reasons=[], capabilities={}),
        ], package_candidates=[
            PackageCandidate(package_id="P1", name="Package One", price_sar=180,
                             matched_needs=[], match_reasons=[], capabilities={"data_gb": 50}),
        ])
        self.service.search_succeeded("REQ-INTEGRATION", self.pool)
        self.plan = SolutionDesignPlan(strategies=[dict(
            strategy_id="S1", decision_policy="REQUIREMENT_FIT", intent="x",
            focus_priorities=[1], intended_tradeoffs=[])])
        self.designs = a.MultipleStrategyDesignResult(results=[], failures=[])
        self.protected = self.snapshot_protected()
        self.addCleanup(self.assert_protected)

    def snapshot_protected(self):
        with closing(self.database._connect()) as connection:
            return {table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                    for table in ("packages", "addons", "customers")}

    def assert_protected(self):
        self.assertEqual(self.snapshot_protected(), self.protected)

    def recommended_result(self):
        s1 = candidate_solution("S1", "P1", [], 180, "WITHIN", "S1 trade-off")
        return a.SolutionPipelineResult(
            plan=self.plan, designs=self.designs, solutions=[s1], guardrails=[guardrail("S1", "PASS")],
            outcome="RECOMMENDED",
            comparison=SolutionComparison(needs_coverage_summary="x", priority_fit_summary="x",
                                          budget_fit_summary="x", key_tradeoff="x"),
            recommendation=SolutionRecommendation(recommended_solution_id="S1", why_recommended="Only option"))

    def no_valid_recommendation_result(self):
        s1 = candidate_solution("S1", "P1", [], 240, "ABOVE", "S1 trade-off")
        return a.SolutionPipelineResult(
            plan=self.plan, designs=self.designs, solutions=[s1], guardrails=[guardrail("S1", "FAIL")],
            outcome="NO_VALID_RECOMMENDATION", comparison=None, recommendation=None)

    def run_design(self):
        return run_solution_design_for_request("REQ-INTEGRATION", request_service=self.service)

    def test_recommended_outcome_persists_all_three_outputs_in_one_transition(self):
        result = self.recommended_result()
        with patch("backend.services.solution_design_workflow.run_solution_design", return_value=result) as agent:
            workflow_result = self.run_design()
        agent.assert_called_once_with(self.requirements, self.pool, llm=None)
        self.assertEqual((workflow_result.state.status, workflow_result.state.stage),
                         (Status.WAITING_FOR_USER, Stage.SELECTION))
        self.assertIs(workflow_result.pipeline_result, result)

        checkpoint = self.service.get_checkpoint("REQ-INTEGRATION")
        self.assertEqual(checkpoint.candidate_solutions, [result.solutions[0].model_dump(mode="json")])
        self.assertEqual(checkpoint.solution_validation, [result.guardrails[0].model_dump(mode="json")])
        self.assertEqual(checkpoint.final_solutions["solutions"][0]["solution_id"], "S1")
        self.assertTrue(checkpoint.final_solutions["solutions"][0]["recommended"])
        # candidate_pool is untouched by this transition; Search already persisted it.
        self.assertEqual(checkpoint.candidate_pool, self.pool)

    def test_no_valid_recommendation_persists_attempt_and_routes_to_adjustment(self):
        result = self.no_valid_recommendation_result()
        with patch("backend.services.solution_design_workflow.run_solution_design", return_value=result):
            workflow_result = self.run_design()
        self.assertEqual((workflow_result.state.status, workflow_result.state.stage),
                         (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS))
        checkpoint = self.service.get_checkpoint("REQ-INTEGRATION")
        self.assertEqual(checkpoint.candidate_solutions, [result.solutions[0].model_dump(mode="json")])
        self.assertEqual(checkpoint.solution_validation, [result.guardrails[0].model_dump(mode="json")])
        self.assertEqual(checkpoint.final_solutions, {"solutions": [], "recommendation_available": False})

    def test_invalid_states_and_repeat_invocation_do_not_call_design(self):
        result = self.recommended_result()
        with patch("backend.services.solution_design_workflow.run_solution_design", return_value=result) as agent:
            self.run_design()
            for status, stage in ((Status.PROCESSING, Stage.SEARCHING), (Status.PROCESSING, Stage.VALIDATING),
                                  (Status.PROCESSING, Stage.PREPARING_SOLUTIONS), (Status.WAITING_FOR_USER, Stage.SELECTION),
                                  (Status.PROCESSING, Stage.APPLYING), (Status.COMPLETED, Stage.COMPLETED),
                                  (Status.ERROR, Stage.ERROR), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS)):
                self.database.update_request("REQ-INTEGRATION", status=status, stage=stage)
                before = self.path.read_bytes()
                with self.assertRaises(InvalidLifecycleTransition):
                    self.run_design()
                self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(agent.call_count, 1)

    def test_missing_candidate_pool_raises_before_calling_agent(self):
        self.database.update_checkpoint("REQ-INTEGRATION", candidate_pool=None)
        with patch("backend.services.solution_design_workflow.run_solution_design") as agent:
            with self.assertRaises(ValueError):
                self.run_design()
        agent.assert_not_called()
        state = self.service.reconstruct_shared_state("REQ-INTEGRATION")
        # Preflight failure: does not mark ERROR.
        self.assertEqual((state.status, state.stage), (Status.PROCESSING, Stage.DESIGNING))

    def test_execution_exceptions_mark_error_and_reraise(self):
        error = RuntimeError("solution design failed")
        with patch("backend.services.solution_design_workflow.run_solution_design", side_effect=error):
            with self.assertRaises(RuntimeError) as caught:
                self.run_design()
        self.assertIs(caught.exception, error)
        state = self.service.reconstruct_shared_state("REQ-INTEGRATION")
        self.assertEqual((state.status, state.stage), (Status.ERROR, Stage.ERROR))
        self.assertEqual(state.candidate_pool, self.pool)


if __name__ == "__main__":
    unittest.main()
