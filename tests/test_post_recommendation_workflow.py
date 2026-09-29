import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.database.db import SearchDatabase
from backend.database.seed_data import seed_data
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool
from backend.schemas.application import ApplySolutionInput
from backend.services.current_plan_service import CurrentPlanService
from backend.services.history_service import HistoryService
from backend.services.request_service import RequestService
from backend.tools.apply_tool import apply_selected_solution


class PostRecommendationWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "telora.sqlite3"
        self.db = SearchDatabase(self.path)
        seed_data(database_path=self.path)
        self.plans = CurrentPlanService(self.db)
        self.requests = RequestService(self.db)
        self.history = HistoryService(self.db)

    def prepared_request(self, request_id="REQ-APPLY", package_id="PKG-CONNECT", addon_ids=None):
        addon_ids = ["ADD-SOC-30"] if addon_ids is None else addon_ids
        requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 300)
        self.requests.create_request(request_id, "CUST-01", requirements)
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE requests SET status='WAITING_FOR_USER', stage='SELECTION' WHERE request_id=?",
                               (request_id,))
            connection.execute("UPDATE workflow_checkpoints SET final_solutions_json=? WHERE request_id=?",
                               (json.dumps({"solutions": [{"solution_id": "SOL-1",
                                "package": {"package_id": package_id},
                                "addons": [{"addon_id": addon_id} for addon_id in addon_ids]}]}), request_id))

    def generated_ready_request(self, package_id, addon_ids):
        requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 300)
        request = self.requests.start_customer_request("CUST-01", requirements)
        self.requests.search_succeeded(request.request_id, CandidatePool(
            package_candidates=[], addon_candidates=[], search_status="SUCCESS"))
        final_solutions = {"solutions": [{"solution_id": "SOL-1", "package": {"package_id": package_id},
                                            "addons": [{"addon_id": addon_id} for addon_id in addon_ids]}]}
        self.requests.solution_design_ready(request.request_id, [], [], final_solutions)
        return request.request_id

    def test_success_updates_current_snapshot_and_preserves_order_history(self):
        self.prepared_request()
        self.requests.select_solution("REQ-APPLY", {"solution_id": "SOL-1"})
        old = self.plans.get_current_plan("CUST-01")
        target_package = next(item for item in self.db.get_packages() if item["package_id"] == "PKG-CONNECT")
        selected_request = self.history.get_request_details("REQ-APPLY", "CUST-01")
        self.assertEqual(selected_request["previous_package_ids"], [old["package_id"]])
        self.assertEqual(selected_request["new_package_ids"], ["PKG-CONNECT"])
        self.assertEqual(selected_request["new_package_names"], [target_package["package_name"]])
        self.assertEqual(selected_request["new_plan"]["package_data_gb"], target_package["data_gb"])
        self.assertEqual(selected_request["new_plan"]["addon_names"], ["Social Boost 30"])
        self.assertEqual(selected_request["total_price"], selected_request["new_plan"]["total_price"])
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ACTIVE")
        plan = self.plans.get_current_plan("CUST-01")
        self.assertEqual(plan["package_id"], "PKG-CONNECT")
        self.assertEqual(plan["addon_ids"], ["ADD-SOC-30"])
        self.assertEqual(plan["addon_names"], ["Social Boost 30"])
        self.assertEqual(plan["total_price"], result["total_price"])
        self.assertEqual(plan["created_at"], old["created_at"])
        self.assertNotEqual(plan["updated_at"], old["updated_at"])
        order = self.history.get_order_details(result["order_id"], "CUST-01")
        self.assertEqual(order["status"], "ACTIVE")
        self.assertEqual(order["previous_package_ids"], [old["package_id"]])
        self.assertEqual([item["status"] for item in self.history.get_order_status_history(
            result["order_id"], "CUST-01")], ["PENDING", "PROCESSING", "ACTIVE"])
        request = self.history.get_request_details("REQ-APPLY", "CUST-01")
        self.assertEqual(request["business_status"], "ACTIVE")
        self.assertEqual(request["order_id"], result["order_id"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM current_plans WHERE customer_id='CUST-01'").fetchone()[0], 1)
            self.assertEqual([row[0] for row in connection.execute(
                "SELECT status FROM order_status_history WHERE order_id=? ORDER BY id", (result["order_id"],))],
                ["PENDING", "PROCESSING", "ACTIVE"])

    def test_activation_failure_keeps_plan_and_records_failed_history(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")

        def fail_activation():
            raise RuntimeError("simulated activation failure")

        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db,
                                         activation=fail_activation)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "FAILED")
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)
        self.assertEqual(self.history.get_order_details(result["order_id"], "CUST-01")["status"], "FAILED")
        request = self.history.get_request_details("REQ-APPLY", "CUST-01")
        self.assertEqual(request["business_status"], "FAILED")
        self.assertEqual(request["previous_package_ids"], [before["package_id"]])
        self.assertEqual(request["new_package_ids"], ["PKG-CONNECT"])

    def test_current_plan_write_failure_rolls_back_activation_and_marks_order_failed(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER reject_plan_update BEFORE UPDATE ON current_plans
                BEGIN SELECT RAISE(ABORT, 'blocked plan write'); END""")
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertFalse(result["success"])
        self.assertEqual(self.history.get_order_details(result["order_id"], "CUST-01")["status"], "FAILED")
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)

    def test_order_insert_failure_marks_request_failed_without_creating_order(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER reject_order_insert BEFORE INSERT ON orders
                BEGIN SELECT RAISE(ABORT, 'blocked order insert'); END""")
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["failure_recorded"])
        self.assertEqual(self.history.get_request_details("REQ-APPLY", "CUST-01")["business_status"], "FAILED")
        self.assertEqual(self.history.admin_order_status_counts(), {})
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)

    def test_request_update_failure_rolls_back_plan_and_active_order(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")
        with sqlite3.connect(self.path) as connection:
            connection.execute("""CREATE TRIGGER reject_request_activation BEFORE UPDATE ON requests
                WHEN NEW.business_status='ACTIVE'
                BEGIN SELECT RAISE(ABORT, 'blocked active request write'); END""")
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "FAILED")
        self.assertTrue(result["failure_recorded"])
        self.assertEqual(self.history.get_order_details(result["order_id"], "CUST-01")["status"], "FAILED")
        self.assertEqual(self.history.get_request_details("REQ-APPLY", "CUST-01")["business_status"], "FAILED")
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)

    def test_failure_bookkeeping_rejection_returns_technical_error_not_false_failed(self):
        for rejected_table, rejected_statement in (
            ("orders", "UPDATE orders SET status='FAILED'"),
            ("requests", "UPDATE requests SET business_status='FAILED'"),
        ):
            with self.subTest(table=rejected_table):
                request_id = f"REQ-BOOKKEEP-{rejected_table}"
                self.prepared_request(request_id)
                before = self.plans.get_current_plan("CUST-01")
                with sqlite3.connect(self.path) as connection:
                    connection.execute(f"""CREATE TRIGGER reject_failed_{rejected_table}
                        BEFORE UPDATE ON {rejected_table}
                        WHEN {"NEW.status='FAILED'" if rejected_table == 'orders' else "NEW.business_status='FAILED'"}
                        BEGIN SELECT RAISE(ABORT, 'blocked failed status write'); END""")
                result = apply_selected_solution("CUST-01", request_id, "SOL-1", package_ids=["PKG-CONNECT"],
                                                 addon_ids=["ADD-SOC-30"], database=self.db,
                                                 activation=lambda: (_ for _ in ()).throw(RuntimeError("activation fault")))
                self.assertFalse(result["success"])
                self.assertEqual(result["status"], "ERROR")
                self.assertFalse(result["failure_recorded"])
                self.assertIn("failure status could not be saved", result["error_message"])
                self.assertEqual(self.plans.get_current_plan("CUST-01"), before)
                with sqlite3.connect(self.path) as connection:
                    order_status = connection.execute("SELECT status FROM orders WHERE request_id=?", (request_id,)).fetchone()
                    request_status = connection.execute("SELECT business_status FROM requests WHERE request_id=?", (request_id,)).fetchone()[0]
                self.assertEqual(order_status[0], "PROCESSING")
                self.assertEqual(request_status, "PROCESSING")
                with sqlite3.connect(self.path) as connection:
                    connection.execute(f"DROP TRIGGER reject_failed_{rejected_table}")

    def test_historical_order_fields_are_immutable_and_lifecycle_still_completes(self):
        self.prepared_request()
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        with sqlite3.connect(self.path) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE orders SET package_ids_json='[]' WHERE order_id=?", (result["order_id"],))
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM orders WHERE order_id=?", (result["order_id"],))
            self.assertEqual(connection.execute("SELECT status FROM orders WHERE order_id=?",
                                                 (result["order_id"],)).fetchone()[0], "ACTIVE")

    def test_customer_history_is_owner_scoped_and_admin_counts_statuses(self):
        self.prepared_request()
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertIsNone(self.history.get_request_details("REQ-APPLY", "CUST-02"))
        self.assertIsNone(self.history.get_order_details(result["order_id"], "CUST-02"))
        self.assertEqual(self.history.admin_request_status_counts()["ACTIVE"], 1)
        self.assertEqual(self.history.admin_order_status_counts()["ACTIVE"], 1)

    def test_start_request_generates_unique_ids_and_snapshots_previous_plan(self):
        requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 300)
        first = self.requests.start_customer_request("CUST-01", requirements)
        second = self.requests.start_customer_request("CUST-01", requirements)
        self.assertNotEqual(first.request_id, second.request_id)
        rows = self.history.get_customer_requests("CUST-01")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["business_status"], "STARTED")
        catalog_name = next(row["name"] for row in self.db.get_customers() if row["customer_id"] == "CUST-01")
        self.assertTrue(all(row["customer_name"] == catalog_name for row in rows))
        self.assertEqual(rows[0]["previous_package_ids"], ["PKG-PLUS"])
        with self.assertRaises(ValueError):
            self.requests.start_customer_request("UNKNOWN", requirements)

    def test_one_customer_can_keep_multiple_historical_orders(self):
        self.prepared_request("REQ-ONE")
        first = apply_selected_solution("CUST-01", "REQ-ONE", "SOL-1", package_ids=["PKG-CONNECT"],
                                        addon_ids=["ADD-SOC-30"], database=self.db)
        self.prepared_request("REQ-TWO")
        second = apply_selected_solution("CUST-01", "REQ-TWO", "SOL-1", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertNotEqual(first["order_id"], second["order_id"])
        self.assertEqual(len(self.history.get_customer_orders("CUST-01")), 2)
        self.assertEqual(self.history.admin_summary_counts()["orders"]["TOTAL"], 2)

    def test_end_to_end_repeat_orders_and_failed_third_request_preserve_latest_plan(self):
        first_request = self.generated_ready_request("PKG-CONNECT", ["ADD-SOC-30"])
        first_solution = self.requests.get_checkpoint(first_request).final_solutions["solutions"][0]
        self.requests.select_solution(first_request, first_solution)
        first_result = apply_selected_solution(
            "CUST-01", first_request, "SOL-1", package_ids=["PKG-CONNECT"],
            addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertTrue(first_result["success"])
        first_order_before = self.history.get_order_details(first_result["order_id"], "CUST-01")

        second_request = self.generated_ready_request("PKG-PLUS", [])
        second_solution = self.requests.get_checkpoint(second_request).final_solutions["solutions"][0]
        self.requests.select_solution(second_request, second_solution)
        second_result = apply_selected_solution(
            "CUST-01", second_request, "SOL-1", package_ids=["PKG-PLUS"], addon_ids=[], database=self.db)
        self.assertTrue(second_result["success"])
        latest_plan = self.plans.get_current_plan("CUST-01")
        self.assertEqual(latest_plan["package_id"], "PKG-PLUS")
        self.assertEqual(latest_plan["package_name"], next(p["package_name"] for p in self.db.get_packages()
                                                             if p["package_id"] == "PKG-PLUS"))
        self.assertEqual(latest_plan["addon_ids"], [])
        self.assertEqual(latest_plan["total_price"], second_result["total_price"])
        self.assertEqual(self.history.get_order_details(first_result["order_id"], "CUST-01"), first_order_before)

        third_request = self.generated_ready_request("PKG-LITE", [])
        third_solution = self.requests.get_checkpoint(third_request).final_solutions["solutions"][0]
        self.requests.select_solution(third_request, third_solution)
        failed_result = apply_selected_solution(
            "CUST-01", third_request, "SOL-1", package_ids=["PKG-LITE"], addon_ids=[], database=self.db,
            activation=lambda: (_ for _ in ()).throw(RuntimeError("activation fault")))
        self.assertFalse(failed_result["success"])
        self.assertEqual(failed_result["status"], "FAILED")
        self.assertEqual(self.plans.get_current_plan("CUST-01"), latest_plan)
        self.assertEqual(self.history.get_order_details(first_result["order_id"], "CUST-01"), first_order_before)
        self.assertEqual(self.history.get_order_details(second_result["order_id"], "CUST-01")["status"], "ACTIVE")
        self.assertEqual(self.history.get_request_details(third_request, "CUST-01")["business_status"], "FAILED")
        self.assertEqual({row["request_id"] for row in self.history.get_customer_requests("CUST-01")},
                         {first_request, second_request, third_request})
        self.assertEqual({row["order_id"] for row in self.history.get_customer_orders("CUST-01")},
                         {first_result["order_id"], second_result["order_id"], failed_result["order_id"]})

    def test_unknown_solution_fails_without_order_or_plan_change(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "UNKNOWN", package_ids=["PKG-CONNECT"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertFalse(result["success"])
        self.assertIsNone(result["order_id"])
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)
        self.assertEqual(self.history.admin_order_status_counts(), {})

    def test_client_component_ids_cannot_change_the_saved_selection(self):
        self.prepared_request()
        before = self.plans.get_current_plan("CUST-01")
        result = apply_selected_solution("CUST-01", "REQ-APPLY", "SOL-1", package_ids=["PKG-PLUS"],
                                         addon_ids=["ADD-SOC-30"], database=self.db)
        self.assertFalse(result["success"])
        self.assertIsNone(result["order_id"])
        self.assertEqual(self.plans.get_current_plan("CUST-01"), before)

    def test_catalog_values_are_authoritative_and_unknown_components_are_rejected(self):
        for package_id, addon_id in (("UNKNOWN-PACKAGE", "ADD-SOC-30"),
                                     ("PKG-CONNECT", "UNKNOWN-ADDON")):
            request_id = f"REQ-{package_id}-{addon_id}"
            self.prepared_request(request_id)
            with sqlite3.connect(self.path) as connection:
                connection.execute("UPDATE workflow_checkpoints SET final_solutions_json=? WHERE request_id=?",
                                   (json.dumps({"solutions": [{"solution_id": "SOL-1",
                                    "package": {"package_id": package_id},
                                    "addons": [{"addon_id": addon_id}]}]}), request_id))
            before = self.plans.get_current_plan("CUST-01")
            result = apply_selected_solution("CUST-01", request_id, "SOL-1", package_ids=[package_id],
                                             addon_ids=[addon_id], database=self.db)
            self.assertFalse(result["success"])
            self.assertEqual(result["status"], "FAILED")
            self.assertIsNone(result["order_id"])
            self.assertEqual(self.plans.get_current_plan("CUST-01"), before)

        with self.assertRaises(ValueError):
            ApplySolutionInput(customer_id="CUST-01", request_id="REQ-X", selected_solution_id="SOL-1",
                               package_ids=["PKG-CONNECT"], addon_ids=[], total_price=0,
                               package_name="client supplied")

    def test_admin_history_filters_and_pagination(self):
        first = self.requests.start_customer_request(
            "CUST-01", Requirements(NeedSelection("Data", "Medium"), None, None, 300))
        second = self.requests.start_customer_request(
            "CUST-02", Requirements(NeedSelection("Calls", "Low"), None, None, 100))
        filtered = self.history.admin_list_requests(customer_id="CUST-01", status="STARTED", limit=1)
        self.assertEqual([item["request_id"] for item in filtered], [first.request_id])
        self.assertEqual(self.history.admin_list_requests(customer_id="CUST-02")[0]["request_id"], second.request_id)
        self.assertEqual(self.history.admin_summary_counts()["requests"]["TOTAL"], 2)


if __name__ == "__main__":
    unittest.main()
