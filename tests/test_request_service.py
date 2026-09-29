"""Service lifecycle tests use isolated SQLite databases and no model clients."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from backend.database.db import SearchDatabase
from backend.database.seed_data import seed_data
from backend.database.workflow_persistence import OUTPUT_FIELDS
from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.request import RequestStatus as Status, WorkflowStage as Stage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool, SearchStatus
from backend.services.request_service import ACTIVE_STATES, InvalidLifecycleTransition, RequestService, TRANSITIONS


class RequestServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = SearchDatabase(Path(directory.name) / "service.sqlite3")
        seed_data(database_path=self.database.path)
        self.service = RequestService(self.database)
        self.requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 220)
        self.pool = CandidatePool(package_candidates=[], addon_candidates=[], search_status="SUCCESS")
        self.customer_id = "CUST-01"
        self.solution = {"solution_id": "S1", "package": {"package_id": "PKG-LITE"}, "addons": []}
        self.request = self.service.create_request("REQ-1", self.customer_id, self.requirements)

    def call_event(self, event, request_id="REQ-1"):
        if event == "select_solution":
            checkpoint = self.service.get_checkpoint(request_id)
            if checkpoint is not None and checkpoint.final_solutions is None:
                self.database.update_checkpoint(request_id, final_solutions={"solutions": [self.solution]})
        args = {
            "search_succeeded": (self.pool,),
            "search_needs_adjustment": (self.pool.model_copy(update={"search_status": SearchStatus.INSUFFICIENT}),),
            "design_completed": ([{"id": "S1"}],),
            "validation_completed": ({"valid": True}, {"solutions": [self.solution]}),
            "solutions_ready": (),
            "solution_design_ready": ([{"id": "S1"}], [{"id": "G1"}], {"solutions": []}),
            "solution_design_needs_adjustment": ([{"id": "S1"}], [{"id": "G1"}], {"solutions": []}),
            "select_solution": (self.solution,),
            "apply_completed": ({"applied": True},),
            "resubmit_requirements": (Requirements(NeedSelection("Calls", "High"), None, None, 300),),
            "cancel_request": (),
            "mark_error": (),
        }
        return getattr(self.service, event)(request_id, *args[event])

    def snapshot(self):
        return self.service.get_request("REQ-1"), self.service.get_checkpoint("REQ-1")

    def test_creation_and_retrieval(self):
        request, checkpoint = self.snapshot()
        self.assertEqual(request, self.request)
        self.assertEqual((request.status, request.stage), (Status.PROCESSING, Stage.SEARCHING))
        self.assertEqual(checkpoint, WorkflowCheckpoint(request_id=request.request_id))
        self.assertEqual(request.created_at, request.updated_at)
        self.assertIsNone(self.service.get_request("missing"))
        self.assertIsNone(self.service.get_checkpoint("missing"))
        before = self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_request("REQ-1", "CUST-02", self.requirements)
        self.assertEqual(self.snapshot(), before)

    def test_creation_failure_rolls_back_request(self):
        with closing(self.database._connect()) as connection:
            connection.execute("""CREATE TRIGGER reject_checkpoint BEFORE INSERT ON workflow_checkpoints
                                  BEGIN SELECT RAISE(ABORT, 'test initialization failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.create_request("REQ-FAIL", self.customer_id, self.requirements)
        self.assertIsNone(self.service.get_request("REQ-FAIL"))
        self.assertIsNone(self.service.get_checkpoint("REQ-FAIL"))

    def test_unknown_customer_cannot_create_request(self):
        with self.assertRaisesRegex(ValueError, "Unknown customer"):
            self.service.create_request("REQ-UNKNOWN", "NOT-A-CUSTOMER", self.requirements)
        self.assertIsNone(self.service.get_request("REQ-UNKNOWN"))

    def test_request_customer_and_order_foreign_keys_reject_unknown_references(self):
        with closing(self.database._connect()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                with connection:
                    connection.execute("""INSERT INTO requests (
                        request_id,customer_id,customer_name,requirements_json,status,stage,created_at,updated_at
                    ) VALUES ('REQ-RAW','UNKNOWN','Unknown','{}','PROCESSING','SEARCHING','2026-01-01','2026-01-01')""")
        with closing(self.database._connect()) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                with connection:
                    connection.execute("UPDATE requests SET order_id='ORD-MISSING' WHERE request_id='REQ-1'")
        self.assertIsNone(self.service.get_request("REQ-RAW"))

    def test_full_success_lifecycle_and_output_retention(self):
        expected_outputs = {}
        events = [
            ("search_succeeded", {"candidate_pool": self.pool}),
            ("design_completed", {"candidate_solutions": [{"id": "S1"}]}),
            ("validation_completed", {"solution_validation": {"valid": True}, "final_solutions": {"solutions": [self.solution]}}),
            ("solutions_ready", {}),
            ("select_solution", {"selected_solution": self.solution}),
            ("apply_completed", {"applied_solution": {"applied": True}}),
        ]
        previous = self.request
        for event, outputs in events:
            with self.subTest(event=event):
                self.call_event(event)
                current, checkpoint = self.snapshot()
                self.assertEqual((current.status, current.stage), TRANSITIONS[event][1])
                self.assertEqual(current.created_at, self.request.created_at)
                self.assertGreater(current.updated_at, previous.updated_at)
                self.assertEqual(current.requirements, self.requirements)
                expected_outputs.update(outputs)
                for field, output in expected_outputs.items():
                    self.assertEqual(getattr(checkpoint, field), output)
                previous = current

    def test_all_event_source_pairs_and_terminal_states(self):
        # Exhaust every status/stage pair, including inconsistent low-level states.
        for status in Status:
            for stage in Stage:
                for event in (*TRANSITIONS, "mark_error"):
                    with self.subTest(status=status, stage=stage, event=event):
                        self.database.update_request("REQ-1", status=status, stage=stage)
                        if event == "select_solution":
                            self.database.update_checkpoint("REQ-1", final_solutions={"solutions": [self.solution]})
                        before = self.snapshot()
                        allowed = ((status, stage) in ACTIVE_STATES if event == "mark_error"
                                   else (status, stage) == TRANSITIONS[event][0])
                        if event == "cancel_request" and (status, stage) == TRANSITIONS[event][1]:
                            # Cancel is idempotent: repeating it on a cancelled request is a no-op.
                            self.assertFalse(self.call_event(event))
                            self.assertEqual(self.snapshot(), before)
                        elif allowed:
                            self.call_event(event)
                            expected = (Status.ERROR, Stage.ERROR) if event == "mark_error" else TRANSITIONS[event][1]
                            current = self.service.get_request("REQ-1")
                            self.assertEqual((current.status, current.stage), expected)
                        else:
                            with self.assertRaises(InvalidLifecycleTransition):
                                self.call_event(event)
                            self.assertEqual(self.snapshot(), before)

    def test_error_preserves_outputs_from_every_active_state(self):
        outputs = {name: {"saved": True} for name in OUTPUT_FIELDS if name != "candidate_pool"}
        self.database.update_checkpoint("REQ-1", candidate_pool=self.pool, **outputs)
        for status, stage in ACTIVE_STATES:
            self.database.update_request("REQ-1", status=status, stage=stage)
            before_request, before_checkpoint = self.snapshot()
            self.service.mark_error("REQ-1")
            request, checkpoint = self.snapshot()
            self.assertEqual(checkpoint, before_checkpoint)
            self.assertEqual(request.created_at, before_request.created_at)
            self.assertGreater(request.updated_at, before_request.updated_at)

    def test_requirements_adjustment_and_atomic_reset(self):
        self.call_event("search_needs_adjustment")
        current = self.service.get_request("REQ-1")
        self.assertEqual((current.status, current.stage), (Status.NEEDS_ADJUSTMENT, Stage.REQUIREMENTS))
        self.assertIsNotNone(self.service.get_checkpoint("REQ-1").candidate_pool)
        # Seed every downstream field to prove all previous derived outputs clear.
        self.database.update_checkpoint("REQ-1", **{
            name: {"old": True} for name in OUTPUT_FIELDS if name != "candidate_pool"})
        before = self.snapshot()
        with closing(self.database._connect()) as connection:
            connection.execute("""CREATE TRIGGER fail_status BEFORE UPDATE OF status ON requests
                                  BEGIN SELECT RAISE(ABORT, 'test reset failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call_event("resubmit_requirements")
        self.assertEqual(self.snapshot(), before)
        with closing(self.database._connect()) as connection:
            connection.execute("DROP TRIGGER fail_status")
        self.call_event("resubmit_requirements")
        request, checkpoint = self.snapshot()
        self.assertEqual(request.request_id, self.request.request_id)
        self.assertEqual(request.customer_id, self.request.customer_id)
        self.assertEqual(request.created_at, self.request.created_at)
        self.assertGreater(request.updated_at, before[0].updated_at)
        self.assertEqual(request.requirements.primary_need, NeedSelection("Calls", "High"))
        self.assertEqual((request.status, request.stage), (Status.PROCESSING, Stage.SEARCHING))
        self.assertEqual(checkpoint, WorkflowCheckpoint(request_id="REQ-1"))
        self.call_event("search_succeeded")

    def test_stage_completion_failure_rolls_back_both_records(self):
        before = self.snapshot()
        with closing(self.database._connect()) as connection:
            connection.execute("""CREATE TRIGGER reject_progress AFTER UPDATE ON requests
                                  BEGIN SELECT RAISE(ABORT, 'test progress failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.call_event("search_succeeded")
        self.assertEqual(self.snapshot(), before)

    def test_bad_outputs_and_missing_records_do_not_progress(self):
        before = self.snapshot()
        for pool in (None, {"invalid": True}):
            with self.assertRaises(ValueError):
                self.service.search_succeeded("REQ-1", pool)
            self.assertEqual(self.snapshot(), before)
        for event in (*TRANSITIONS, "mark_error"):
            with self.assertRaises(KeyError):
                self.call_event(event, "missing")
        with closing(self.database._connect()) as connection, connection:
            connection.execute("DELETE FROM workflow_checkpoints WHERE request_id = 'REQ-1'")
        with self.assertRaises(KeyError):
            self.call_event("search_succeeded")
        self.assertEqual(self.service.get_request("REQ-1"), before[0])

    def test_concurrent_duplicate_events_cannot_both_succeed(self):
        def complete():
            try:
                self.call_event("search_succeeded")
                return "completed"
            except InvalidLifecycleTransition:
                return "rejected"
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: complete(), range(2)))
        self.assertCountEqual(outcomes, ["completed", "rejected"])
        self.assertEqual(self.service.get_request("REQ-1").stage, Stage.DESIGNING)
        self.assertEqual(self.service.get_checkpoint("REQ-1").candidate_pool, self.pool)


if __name__ == "__main__":
    unittest.main()
