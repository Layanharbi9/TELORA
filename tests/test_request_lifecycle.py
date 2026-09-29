"""Request lifecycle: customer status mapping, post-generation Cancel, View solutions,
interrupted-build recovery, atomic solution-ready transition and SSE termination."""

import itertools
import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from backend.api import app as app_module
from backend.database.db import SearchDatabase
from backend.database.seed_data import seed_data
from backend.schemas.request import RequestStatus, WorkflowStage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool
from backend.services.current_plan_service import CurrentPlanService
from backend.services.executor import CLI_EXECUTOR_ID, WEB_EXECUTOR_ID
from backend.services.request_service import (
    INTERRUPTED_MESSAGE, InvalidLifecycleTransition, RequestService,
)

ROOT = Path(__file__).resolve().parents[1]


def _solution(solution_id, recommended=False):
    return {"solution_id": solution_id,
            "package": {"package_id": "PKG-CONNECT", "name": "Connect", "price_sar": 90, "capabilities": {}},
            "addons": [{"addon_id": "ADD-SOC-30", "name": "Social Boost 30", "price_sar": 15}],
            "total_price_sar": 105, "budget_status": "WITHIN_BUDGET", "needs_coverage": [],
            "trade_off": "x", "recommended": recommended,
            "recommendation_reason": "Because" if recommended else None}


FINAL = {"solutions": [_solution("SOL-1", True), _solution("SOL-2")], "recommendation_available": True}


def _passthrough(_name, action, **_kwargs):
    return action()


class LifecycleDatabaseCase(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "telora.sqlite3"
        self.db = SearchDatabase(self.path)
        seed_data(database_path=self.path)
        self.service = RequestService(self.db)
        self.plans = CurrentPlanService(self.db)

    def requirements(self):
        return Requirements(NeedSelection("Data", "Medium"), NeedSelection("Calls", "Low"), None, 300)

    def new_request(self, executor_id=None):
        return self.service.start_customer_request("CUST-01", self.requirements(),
                                                   executor_id=executor_id).request_id

    def ready_request(self):
        request_id = self.new_request()
        self.service.search_succeeded(request_id, CandidatePool(
            package_candidates=[], addon_candidates=[], search_status="SUCCESS"))
        self.service.solution_design_ready(request_id, [], [], FINAL)
        return request_id

    def force(self, request_id, status, stage, **columns):
        assignments = ", ".join(["status=?", "stage=?", *(f"{name}=?" for name in columns)])
        with sqlite3.connect(self.path) as connection:
            connection.execute(f"UPDATE requests SET {assignments} WHERE request_id=?",
                               (status, stage, *columns.values(), request_id))

    def row(self, request_id):
        with sqlite3.connect(self.path) as connection:
            connection.row_factory = sqlite3.Row
            return dict(connection.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone())

    def order_count(self):
        with sqlite3.connect(self.path) as connection:
            return connection.execute("SELECT count(*) FROM orders").fetchone()[0]

    def client(self, customer_id="CUST-01"):
        client = TestClient(app_module.app)
        client.cookies.set(app_module.SESSION_COOKIE, customer_id)
        for patcher in (patch.object(app_module, "_database", return_value=self.db),
                        patch.object(app_module, "traced_action", side_effect=_passthrough)):
            patcher.start()
            self.addCleanup(patcher.stop)
        return client


# ---------------------------------------------------------------------------
class StatusMappingTests(unittest.TestCase):
    def status(self, status, stage):
        return app_module._customer_status({"request_id": "REQ-1", "status": status, "stage": stage})

    def test_required_mapping(self):
        expected = {
            ("PROCESSING", "SEARCHING"): "processing", ("PROCESSING", "DESIGNING"): "processing",
            ("WAITING_FOR_USER", "SELECTION"): "awaiting_selection",
            ("PROCESSING", "APPLYING"): "applying", ("COMPLETED", "COMPLETED"): "completed",
            ("CANCELLED", "CANCELLED"): "cancelled", ("ERROR", "ERROR"): "failed",
            ("NEEDS_ADJUSTMENT", "REQUIREMENTS"): "needs_adjustment",
        }
        for (status, stage), label in expected.items():
            with self.subTest(status=status, stage=stage):
                self.assertEqual(self.status(status, stage), label)

    def test_waiting_with_legacy_started_business_status_is_not_processing(self):
        row = {"request_id": "REQ-1", "status": "WAITING_FOR_USER", "stage": "SELECTION",
               "business_status": "STARTED"}
        self.assertEqual(app_module._customer_status(row), "awaiting_selection")

    def test_no_non_processing_or_unknown_state_falls_back_to_processing(self):
        for status, stage in itertools.product(RequestStatus, WorkflowStage):
            if status is RequestStatus.PROCESSING and stage in (
                    WorkflowStage.SEARCHING, WorkflowStage.DESIGNING,
                    WorkflowStage.VALIDATING, WorkflowStage.PREPARING_SOLUTIONS):
                continue
            with self.subTest(status=status.value, stage=stage.value):
                self.assertNotEqual(self.status(status.value, stage.value), "processing")
        self.assertEqual(self.status("SOMETHING", "ELSE"), "failed")


class RequestsApiTests(LifecycleDatabaseCase):
    def test_requests_endpoint_uses_workflow_state(self):
        waiting = self.ready_request()
        adjusted = self.new_request()
        self.service.search_needs_adjustment(adjusted, CandidatePool(
            package_candidates=[], addon_candidates=[], search_status="INSUFFICIENT"))
        self.assertEqual(self.row(adjusted)["business_status"], "STARTED")  # legacy business status kept
        body = {item["request_id"]: item for item in self.client().get("/api/me/requests").json()}
        self.assertEqual(body[waiting]["status"], "awaiting_selection")
        self.assertEqual(body[waiting]["solutions_count"], 2)
        self.assertEqual(body[waiting]["budget"], 300)
        self.assertEqual([n["need"] for n in body[waiting]["needs"]], ["Data", "Calls"])
        self.assertEqual(body[adjusted]["status"], "needs_adjustment")


# ---------------------------------------------------------------------------
class CancelTests(LifecycleDatabaseCase):
    def test_cancel_is_terminal_and_touches_only_the_request(self):
        request_id = self.ready_request()
        plan_before, checkpoint_before = self.plans.get_current_plan("CUST-01"), self.service.get_checkpoint(request_id)
        self.assertTrue(self.service.cancel_request(request_id))
        row = self.row(request_id)
        self.assertEqual((row["status"], row["stage"]), ("CANCELLED", "CANCELLED"))
        self.assertEqual(row["business_status"], "CANCELLED")
        self.assertIsNotNone(row["completed_at"])
        self.assertIsNone(row["selected_solution_id"])
        self.assertEqual(json.loads(row["recommended_solution_ids_json"]), ["SOL-1", "SOL-2"])
        self.assertEqual(self.service.get_checkpoint(request_id), checkpoint_before)
        self.assertEqual(self.plans.get_current_plan("CUST-01"), plan_before)
        self.assertEqual(self.order_count(), 0)

    def test_cancel_twice_is_idempotent(self):
        request_id = self.ready_request()
        self.assertTrue(self.service.cancel_request(request_id))
        first = self.row(request_id)
        self.assertFalse(self.service.cancel_request(request_id))
        self.assertEqual(self.row(request_id), first)
        response = self.client().post(f"/api/requests/{request_id}/cancel")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["changed"])

    def test_concurrent_duplicate_cancels_resolve_safely(self):
        request_id = self.ready_request()
        results, errors, barrier = [], [], threading.Barrier(2)

        def cancel():
            barrier.wait()
            try:
                results.append(RequestService(SearchDatabase(self.path)).cancel_request(request_id))
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)

        threads = [threading.Thread(target=cancel) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(self.row(request_id)["status"], "CANCELLED")

    def test_cancel_rejected_outside_waiting_for_selection(self):
        for status, stage in (("PROCESSING", "SEARCHING"), ("PROCESSING", "DESIGNING"),
                              ("PROCESSING", "APPLYING"), ("COMPLETED", "COMPLETED"),
                              ("ERROR", "ERROR"), ("NEEDS_ADJUSTMENT", "REQUIREMENTS")):
            with self.subTest(status=status, stage=stage):
                request_id = self.new_request()
                self.force(request_id, status, stage)
                before = self.row(request_id)
                with self.assertRaises(InvalidLifecycleTransition):
                    self.service.cancel_request(request_id)
                self.assertEqual(self.row(request_id), before)
                self.assertEqual(self.client().post(f"/api/requests/{request_id}/cancel").status_code, 409)

    def test_apply_after_cancel_is_rejected_before_any_mutation(self):
        request_id = self.ready_request()
        self.service.cancel_request(request_id)
        plan_before, row_before = self.plans.get_current_plan("CUST-01"), self.row(request_id)
        response = self.client().post(f"/api/requests/{request_id}/apply", json={"solution_id": "SOL-1"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.order_count(), 0)
        self.assertEqual(self.plans.get_current_plan("CUST-01"), plan_before)
        self.assertEqual(self.row(request_id), row_before)

    def test_cancel_after_apply_or_while_applying_is_rejected(self):
        applied = self.ready_request()
        client = self.client()
        self.assertEqual(client.post(f"/api/requests/{applied}/apply", json={"solution_id": "SOL-1"}).status_code, 200)
        self.assertEqual(client.post(f"/api/requests/{applied}/cancel").status_code, 409)
        self.assertEqual(self.row(applied)["status"], "COMPLETED")

        applying = self.ready_request()
        self.service.select_solution(applying, FINAL["solutions"][0])
        self.assertEqual(client.post(f"/api/requests/{applying}/cancel").status_code, 409)
        self.assertEqual((self.row(applying)["status"], self.row(applying)["stage"]), ("PROCESSING", "APPLYING"))

    def test_mark_error_cannot_overwrite_cancelled(self):
        request_id = self.ready_request()
        self.service.cancel_request(request_id)
        with self.assertRaises(InvalidLifecycleTransition):
            self.service.mark_error(request_id)
        self.assertEqual(self.row(request_id)["status"], "CANCELLED")

    def test_cancel_is_owner_scoped(self):
        request_id = self.ready_request()
        self.assertEqual(self.client("CUST-02").post(f"/api/requests/{request_id}/cancel").status_code, 404)
        self.assertEqual(self.client().post("/api/requests/REQ-MISSING/cancel").status_code, 404)
        self.assertEqual(self.row(request_id)["status"], "WAITING_FOR_USER")


# ---------------------------------------------------------------------------
class ViewSolutionsTests(LifecycleDatabaseCase):
    def events(self, client, request_id):
        events = []
        with client.stream("GET", f"/api/build/{request_id}/events") as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    events.append(json.loads(line[len("data: "):]))
        return events

    def test_waiting_request_replays_persisted_solutions_without_regenerating(self):
        request_id = self.ready_request()
        client = self.client()
        with patch.object(app_module, "build_workflow_graph") as graph, \
             patch("backend.services.solution_design_workflow.run_solution_design") as design:
            events = self.events(client, request_id)
        graph.assert_not_called()
        design.assert_not_called()
        done = events[-1]
        self.assertEqual((done["stage"], done["status"]), ("done", "done"))
        self.assertEqual([s["solution_id"] for s in done["data"]["solutions"]], ["SOL-1", "SOL-2"])
        self.assertEqual(self.row(request_id)["status"], "WAITING_FOR_USER")

        response = client.post(f"/api/requests/{request_id}/apply", json={"solution_id": "SOL-2"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ACTIVE")
        self.assertEqual(self.plans.get_current_plan("CUST-01")["package_id"], "PKG-CONNECT")

    def test_other_customer_cannot_replay(self):
        request_id = self.ready_request()
        self.assertEqual(self.client("CUST-02").get(f"/api/build/{request_id}/events").status_code, 404)


# ---------------------------------------------------------------------------
class StaleRecoveryTests(LifecycleDatabaseCase):
    OLD_WEB = "web:previous-boot"

    def test_abandoned_builds_fail_and_everything_else_is_untouched(self):
        abandoned = {}
        for label, stage, executor in (("old-designing", "DESIGNING", self.OLD_WEB),
                                       ("old-searching", "SEARCHING", self.OLD_WEB),
                                       ("legacy", "DESIGNING", None)):
            request_id = self.new_request(executor)
            self.force(request_id, "PROCESSING", stage)
            abandoned[label] = request_id
        kept = {}
        for label, status, stage, executor in (
                ("current", "PROCESSING", "DESIGNING", WEB_EXECUTOR_ID),
                ("cli", "PROCESSING", "SEARCHING", CLI_EXECUTOR_ID),
                ("waiting", "WAITING_FOR_USER", "SELECTION", None),
                ("adjust", "NEEDS_ADJUSTMENT", "REQUIREMENTS", None),
                ("completed", "COMPLETED", "COMPLETED", None),
                ("cancelled", "CANCELLED", "CANCELLED", None),
                ("applying", "PROCESSING", "APPLYING", None)):
            request_id = self.new_request(executor)
            self.force(request_id, status, stage)
            kept[label] = (request_id, self.row(request_id))

        reconciled = self.service.reconcile_interrupted_requests(WEB_EXECUTOR_ID)
        self.assertEqual(sorted(reconciled), sorted(abandoned.values()))
        for label, request_id in abandoned.items():
            with self.subTest(abandoned=label):
                row = self.row(request_id)
                self.assertEqual((row["status"], row["stage"], row["business_status"]), ("ERROR", "ERROR", "FAILED"))
                self.assertEqual(row["error_message"], INTERRUPTED_MESSAGE)
                self.assertIsNotNone(row["completed_at"])
        for label, (request_id, before) in kept.items():
            with self.subTest(kept=label):
                self.assertEqual(self.row(request_id), before)
        self.assertEqual(self.service.reconcile_interrupted_requests(WEB_EXECUTOR_ID), [])

    def test_startup_hook_reconciles(self):
        request_id = self.new_request(self.OLD_WEB)
        self.force(request_id, "PROCESSING", "DESIGNING")
        with patch.object(app_module, "_database", return_value=self.db):
            app_module.initialize_database()
        self.assertEqual(self.row(request_id)["status"], "ERROR")

    def run_build(self, invoke):
        graph = Mock()
        graph.invoke.side_effect = invoke
        with patch.object(app_module, "_database", return_value=self.db), \
             patch.object(app_module, "traced_action", side_effect=_passthrough), \
             patch.object(app_module, "build_workflow_graph", return_value=graph):
            app_module._run_build("REQ-BUILD", "CUST-01", self.requirements())
        return self.row("REQ-BUILD")

    def test_graph_returning_while_still_processing_is_failed(self):
        row = self.run_build(lambda state: {})
        self.assertEqual(row["executor_id"], WEB_EXECUTOR_ID)
        self.assertEqual((row["status"], row["stage"], row["business_status"]), ("ERROR", "ERROR", "FAILED"))
        self.assertEqual(row["error_message"], app_module.GENERIC_FAILURE)

    def test_graph_exception_keeps_existing_failure_message(self):
        def invoke(state):
            raise type("APIConnectionError", (Exception,), {})()
        row = self.run_build(invoke)
        self.assertEqual(row["status"], "ERROR")
        self.assertIn("connection issue", row["error_message"])

    def test_base_exception_while_alive_cannot_leave_processing(self):
        def invoke(state):
            raise SystemExit("worker thread stopped")
        with self.assertRaises(SystemExit):
            self.run_build(invoke)
        row = self.row("REQ-BUILD")
        self.assertEqual((row["status"], row["business_status"]), ("ERROR", "FAILED"))

    def test_guard_leaves_waiting_and_adjustment_untouched(self):
        def ready(state):
            self.service.search_succeeded("REQ-BUILD", CandidatePool(
                package_candidates=[], addon_candidates=[], search_status="SUCCESS"))
            self.service.solution_design_ready("REQ-BUILD", [], [], FINAL)
        row = self.run_build(ready)
        self.assertEqual((row["status"], row["business_status"]), ("WAITING_FOR_USER", "SOLUTIONS_PRESENTED"))
        self.assertIsNone(row["error_message"])

    def test_migration_adds_nullable_executor_to_existing_rows(self):
        request_id = self.new_request()
        with sqlite3.connect(self.path) as connection:
            connection.execute("ALTER TABLE requests DROP COLUMN executor_id")
            connection.execute("PRAGMA user_version = 15")
        SearchDatabase(self.path)
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 16)
        self.assertIsNone(self.row(request_id)["executor_id"])
        self.assertEqual(self.service.reconcile_interrupted_requests(WEB_EXECUTOR_ID), [request_id])


# ---------------------------------------------------------------------------
class AtomicSolutionReadyTests(LifecycleDatabaseCase):
    def test_ready_state_and_business_status_are_written_together(self):
        request_id = self.new_request()
        self.service.search_succeeded(request_id, CandidatePool(
            package_candidates=[], addon_candidates=[], search_status="SUCCESS"))
        with patch.object(self.db, "record_recommendations", side_effect=AssertionError("separate write")):
            self.service.solution_design_ready(request_id, [], [], FINAL)
        row = self.row(request_id)
        self.assertEqual((row["status"], row["stage"], row["business_status"]),
                         ("WAITING_FOR_USER", "SELECTION", "SOLUTIONS_PRESENTED"))
        self.assertEqual(json.loads(row["recommended_solution_ids_json"]), ["SOL-1", "SOL-2"])

    def test_failed_business_write_rolls_back_the_whole_transition(self):
        request_id = self.new_request()
        self.service.search_succeeded(request_id, CandidatePool(
            package_candidates=[], addon_candidates=[], search_status="SUCCESS"))
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER block_presented BEFORE UPDATE OF business_status ON requests
                WHEN NEW.business_status='SOLUTIONS_PRESENTED' BEGIN SELECT RAISE(ABORT, 'blocked'); END""")
        with self.assertRaises(sqlite3.DatabaseError):
            self.service.solution_design_ready(request_id, [], [], FINAL)
        row = self.row(request_id)
        self.assertEqual((row["status"], row["stage"], row["business_status"]), ("PROCESSING", "DESIGNING", "STARTED"))
        self.assertIsNone(self.service.get_checkpoint(request_id).final_solutions)


# ---------------------------------------------------------------------------
class SseTerminalTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app_module.app)
        self.client.cookies.set(app_module.SESSION_COOKIE, "CUST-1")
        patcher = patch.object(app_module, "_database", return_value=Mock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_completed_and_cancelled_end_the_stream(self):
        from backend.schemas.checkpoint import WorkflowCheckpoint
        from backend.schemas.request import Request as RequestRecord
        requirements = Requirements(NeedSelection("Calls", "Medium"), None, None, 100)
        for status, stage in ((RequestStatus.COMPLETED, WorkflowStage.COMPLETED),
                              (RequestStatus.CANCELLED, WorkflowStage.CANCELLED)):
            with self.subTest(status=status.value):
                row = RequestRecord(request_id="REQ-1", customer_id="CUST-1", requirements=requirements,
                                    status=status, stage=stage)
                with patch.object(RequestService, "get_request", return_value=row), \
                     patch.object(RequestService, "get_checkpoint",
                                  return_value=WorkflowCheckpoint(request_id="REQ-1")):
                    with self.client.stream("GET", "/api/build/REQ-1/events") as response:
                        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
                self.assertEqual(events, [{"request_id": "REQ-1", "stage": "done", "status": status.value.lower()}])


# ---------------------------------------------------------------------------
class FrontendLifecycleGuardTests(unittest.TestCase):
    """Source-level guard: no JS test runner exists in this repo."""

    def source(self, name):
        return (ROOT / "frontend" / "web" / "js" / name).read_text(encoding="utf-8")

    def test_requests_page_labels(self):
        app = self.source("app.js")
        for text in ("awaiting_selection:'Awaiting selection'", "applying:'Applying'", "cancelled:'Cancelled'",
                     "needs_adjustment:'Needs adjustment'", "TELORA is building solutions",
                     "Applying your selected plan", "No fitting solution. Adjust your needs or budget.",
                     "generated · `:''}No change applied", "View solutions", "data-cancel-req"):
            self.assertIn(text, app)
        self.assertNotIn("not_applied", app)

    def test_cancel_requires_confirmation_and_never_happens_on_leave(self):
        app, design, api = self.source("app.js"), self.source("design.js"), self.source("api.js")
        self.assertIn("Cancel this request?", app)
        self.assertIn("Your current plan will stay as it is.", app)
        self.assertIn("Keep request", app)
        self.assertIn("data-cancel-request", design)
        for source in (app, design, api):
            for leave_hook in ("beforeunload", "pagehide", "visibilitychange", "sendBeacon"):
                self.assertNotIn(leave_hook, source)
        self.assertEqual(api.count("/cancel`"), 1)


if __name__ == "__main__":
    unittest.main()
