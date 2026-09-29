"""Reconstruction uses persisted fixtures only; no agent/model execution."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

from backend.database.db import SearchDatabase
from backend.database.seed_data import seed_data
from backend.database.workflow_persistence import OUTPUT_FIELDS
from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.request import RequestStatus, WorkflowStage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool, PackageCandidate, SearchNeed, SearchStatus
from backend.schemas.state import SharedWorkflowState
from backend.services.request_service import RequestService, StateReconstructionError


class SharedStateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state.sqlite3"
        self.database = SearchDatabase(self.path)
        seed_data(database_path=self.path)
        self.service = RequestService(self.database)
        self.requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 220)
        self.request = self.service.create_request("REQ-1", "CUST-01", self.requirements)
        self.pool = CandidatePool(package_candidates=[PackageCandidate(
            package_id="PKG-1", name="Example", price_sar=200,
            matched_needs=[SearchNeed(need="Data", level="Medium", priority=1)],
            match_reasons=["Data available"], capabilities={"data_gb": 100})],
            addon_candidates=[], search_status=SearchStatus.SUCCESS)

    def restore(self):
        return self.service.reconstruct_shared_state("REQ-1")

    def test_new_request_and_read_only_reconstruction(self):
        before = self.path.read_bytes()
        state = self.restore()
        self.assertIsInstance(state, SharedWorkflowState)
        self.assertEqual(state.request_id, self.request.request_id)
        self.assertEqual(state.customer_id, self.request.customer_id)
        self.assertIsInstance(state.requirements, Requirements)
        self.assertEqual(state.requirements, self.request.requirements)
        self.assertEqual((state.status, state.stage), (RequestStatus.PROCESSING, WorkflowStage.SEARCHING))
        for name in OUTPUT_FIELDS:
            self.assertIsNone(getattr(state, name))
        self.assertEqual(self.restore(), state)
        self.assertEqual(self.path.read_bytes(), before)

    def test_search_success_and_runtime_edits_do_not_persist(self):
        old = self.restore()
        self.service.search_succeeded("REQ-1", self.pool)
        restored = self.restore()
        self.assertEqual((restored.status, restored.stage), (RequestStatus.PROCESSING, WorkflowStage.DESIGNING))
        self.assertEqual(restored.candidate_pool, self.pool)
        self.assertIsNone(old.candidate_pool)
        restored.candidate_pool.package_candidates[0].capabilities["data_gb"] = 999
        restored.customer_id = "runtime-only"
        self.assertEqual(self.restore().candidate_pool, self.pool)
        self.assertEqual(self.restore().customer_id, "CUST-01")

    def test_later_outputs_waiting_and_completed_restore(self):
        self.service.search_succeeded("REQ-1", self.pool)
        self.service.design_completed("REQ-1", [{"id": "S1"}])
        self.assertEqual(self.restore().candidate_solutions, [{"id": "S1"}])
        final_solutions = {"solutions": [{"solution_id": "S1", "package": {"package_id": "PKG-LITE"}, "addons": []}]}
        self.service.validation_completed("REQ-1", {"valid": True}, final_solutions)
        self.assertEqual(self.restore().stage, WorkflowStage.PREPARING_SOLUTIONS)
        self.service.solutions_ready("REQ-1")
        state = self.restore()
        self.assertEqual((state.status, state.stage), (RequestStatus.WAITING_FOR_USER, WorkflowStage.SELECTION))
        self.assertEqual(state.solution_validation, {"valid": True})
        self.assertEqual(state.final_solutions, final_solutions)
        self.service.select_solution("REQ-1", final_solutions["solutions"][0])
        self.assertEqual(self.restore().selected_solution, final_solutions["solutions"][0])
        self.service.apply_completed("REQ-1", {"receipt": "R1"})
        # A fresh service/database instance uses exactly the same restoration path.
        state = RequestService(SearchDatabase(self.path)).reconstruct_shared_state("REQ-1")
        self.assertEqual((state.status, state.stage), (RequestStatus.COMPLETED, WorkflowStage.COMPLETED))
        checkpoint = self.service.get_checkpoint("REQ-1")
        for name in OUTPUT_FIELDS:
            self.assertEqual(getattr(state, name), getattr(checkpoint, name))

    def test_adjustment_and_requirements_reset(self):
        self.service.search_needs_adjustment("REQ-1", self.pool)
        state = self.restore()
        self.assertEqual((state.status, state.stage), (RequestStatus.NEEDS_ADJUSTMENT, WorkflowStage.REQUIREMENTS))
        self.assertEqual(state.candidate_pool, self.pool)
        self.database.update_checkpoint("REQ-1", **{
            name: {"old": True} for name in OUTPUT_FIELDS if name != "candidate_pool"})
        replacement = Requirements(NeedSelection("Calls", "High"), None, None, 300)
        self.service.resubmit_requirements("REQ-1", replacement)
        state = self.restore()
        self.assertEqual(state.request_id, "REQ-1")
        self.assertEqual(state.requirements, replacement)
        self.assertEqual((state.status, state.stage), (RequestStatus.PROCESSING, WorkflowStage.SEARCHING))
        self.assertTrue(all(getattr(state, name) is None for name in OUTPUT_FIELDS))

    def test_error_preserves_previous_outputs(self):
        self.service.search_succeeded("REQ-1", self.pool)
        self.service.mark_error("REQ-1")
        state = self.restore()
        self.assertEqual((state.status, state.stage), (RequestStatus.ERROR, WorkflowStage.ERROR))
        self.assertEqual(state.candidate_pool, self.pool)

    def test_missing_request_and_checkpoint_are_explicit(self):
        with self.assertRaisesRegex(KeyError, "Missing request"):
            self.service.reconstruct_shared_state("missing")
        with closing(self.database._connect()) as connection, connection:
            connection.execute("DELETE FROM workflow_checkpoints WHERE request_id = 'REQ-1'")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(StateReconstructionError, "Missing checkpoint"):
            self.restore()
        self.assertEqual(self.path.read_bytes(), before)

    def test_mismatched_record_ids_are_rejected(self):
        for request, checkpoint in (
            (self.request, WorkflowCheckpoint(request_id="other")),
            (self.request.model_copy(update={"request_id": "other"}), WorkflowCheckpoint(request_id="REQ-1")),
        ):
            with patch.object(self.database, "get_workflow_records", return_value=(request, checkpoint)):
                with self.assertRaisesRegex(StateReconstructionError, "Mismatched"):
                    self.restore()

    def test_corrupt_json_and_invalid_schema_are_not_repaired(self):
        for value in ("not json", '{"bad":true}'):
            with self.subTest(value=value):
                with closing(self.database._connect()) as connection, connection:
                    connection.execute("UPDATE workflow_checkpoints SET candidate_pool_json = ?", (value,))
                before = self.path.read_bytes()
                with self.assertRaises(StateReconstructionError) as caught:
                    self.restore()
                self.assertIsNotNone(caught.exception.__cause__)
                self.assertEqual(self.path.read_bytes(), before)
        with closing(self.database._connect()) as connection, connection:
            connection.execute("UPDATE workflow_checkpoints SET candidate_pool_json = NULL")
            connection.execute("UPDATE requests SET status = 'INVALID'")
        with self.assertRaises(StateReconstructionError):
            self.restore()

    def test_shared_schema_contains_business_fields_only(self):
        self.assertEqual(set(SharedWorkflowState.model_fields), {
            "request_id", "customer_id", "status", "stage", "requirements", *OUTPUT_FIELDS})
        data = self.restore().model_dump()
        for name in ("search_trace", "search_plan", "judge_history", "tool_traces", "retry_metadata",
                     "stages", "created_at", "updated_at"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                SharedWorkflowState.model_validate({**data, name: {}})
        for value in ("scalar", {"bad": object()}, {"bad": float("nan")}):
            with self.assertRaises(ValueError):
                SharedWorkflowState.model_validate({**data, "applied_solution": value})

    def test_records_are_read_from_one_snapshot_during_concurrent_update(self):
        # WAL allows a writer to commit between the two SELECTs while the reader
        # must continue seeing the original, internally consistent snapshot.
        with closing(self.database._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
        writer = RequestService(SearchDatabase(self.path))
        reader = self.database._connect()
        triggered = []

        def on_query(sql):
            if sql.startswith("SELECT * FROM workflow_checkpoints"):
                writer.search_succeeded("REQ-1", self.pool)
                triggered.append(True)

        reader.set_trace_callback(on_query)
        with patch.object(self.database, "_connect", return_value=reader):
            state = self.restore()
        self.assertEqual(triggered, [True])
        self.assertEqual(state.stage, WorkflowStage.SEARCHING)
        self.assertIsNone(state.candidate_pool)
        fresh = self.restore()
        self.assertEqual(fresh.stage, WorkflowStage.DESIGNING)
        self.assertEqual(fresh.candidate_pool, self.pool)


if __name__ == "__main__":
    unittest.main()
