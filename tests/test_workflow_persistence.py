import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from pydantic import ValidationError

from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase
from backend.database.migrations import SCHEMA_VERSION, migrate
from backend.database.seed_data import seed_data
from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.request import Request, RequestStatus, WorkflowStage
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool, PackageCandidate, SearchNeed


class WorkflowPersistenceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "workflow.sqlite3"
        self.database = SearchDatabase(self.path)
        seed_data(database_path=self.path)
        self.request = Request(
            request_id="REQ-1", customer_id="CUST-01",
            requirements=Requirements(NeedSelection("Data", "Medium"),
                                      NeedSelection("Streaming", "High"), None, 220),
        )
        self.database.create_request(self.request)

    def pool(self):
        return CandidatePool(
            package_candidates=[PackageCandidate(
                package_id="PKG-1", name="Example", price_sar=200,
                matched_needs=[SearchNeed(need="Data", level="Medium", priority=1)],
                match_reasons=["Data available"], capabilities={"data_gb": 100})],
            addon_candidates=[], search_status="SUCCESS", search_notes=["Example"],
        )

    def test_request_roundtrip_and_progress_updates(self):
        loaded = self.database.get_request("REQ-1")
        self.assertEqual(loaded, self.request)
        self.assertIsInstance(loaded.requirements, Requirements)
        self.assertEqual(loaded.requirements.to_dict(), self.request.requirements.to_dict())
        for status in RequestStatus:
            self.database.update_request("REQ-1", status=status)
            updated = self.database.get_request("REQ-1")
            self.assertEqual(updated.status, status)
            self.assertEqual(updated.created_at, loaded.created_at)
            self.assertGreater(updated.updated_at, loaded.updated_at)
            loaded = updated
        for stage in WorkflowStage:
            self.database.update_request("REQ-1", stage=stage)
            self.assertEqual(self.database.get_request("REQ-1").stage, stage)
        self.assertIsNone(self.database.get_request("absent"))
        with self.assertRaises(KeyError):
            self.database.update_request("absent", status=RequestStatus.ERROR)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_request(self.request)
        before = self.database.get_request("REQ-1")
        with self.assertRaises(ValueError):
            self.database.update_request("REQ-1", status="SUCCESS")
        self.assertEqual(self.database.get_request("REQ-1"), before)

    def test_checkpoint_roundtrip_partial_updates_and_nulls(self):
        empty = WorkflowCheckpoint(request_id="REQ-1")
        self.database.create_checkpoint(empty)
        self.assertEqual(self.database.get_checkpoint("REQ-1"), empty)
        self.database.update_checkpoint("REQ-1", candidate_pool=self.pool(),
                                        candidate_solutions=[{"id": "S1", "items": [1, True, None]}])
        checkpoint = self.database.get_checkpoint("REQ-1")
        self.assertEqual(checkpoint.candidate_pool, self.pool())
        self.assertIsNone(checkpoint.applied_solution)
        self.database.update_checkpoint("REQ-1", selected_solution={"id": "S1"})
        self.assertEqual(self.database.get_checkpoint("REQ-1").candidate_pool, self.pool())
        self.database.update_checkpoint("REQ-1", selected_solution=None)
        self.assertIsNone(self.database.get_checkpoint("REQ-1").selected_solution)
        self.assertIsNone(self.database.get_checkpoint("absent"))
        with self.assertRaises(KeyError):
            self.database.update_checkpoint("absent", candidate_pool=None)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_checkpoint(WorkflowCheckpoint(request_id="absent"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_checkpoint(empty)

    def test_checkpoint_rejects_metadata_and_non_json_outputs(self):
        fields = {"request_id", "candidate_pool", "candidate_solutions", "solution_validation",
                  "final_solutions", "selected_solution", "applied_solution"}
        self.assertEqual(set(WorkflowCheckpoint.model_fields), fields)
        with closing(self.database._connect()) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(workflow_checkpoints)")}
        self.assertEqual(columns, {"request_id"} | {name + "_json" for name in fields - {"request_id"}})
        for field in ("customer_id", "requirements", "status", "stage", "created_at", "updated_at"):
            with self.assertRaises(ValidationError):
                WorkflowCheckpoint(request_id="REQ-1", **{field: "invalid"})
            with self.assertRaises(ValueError):
                self.database.update_checkpoint("REQ-1", **{field: "invalid"})
        for value in ("scalar", {"value": object()}, {"value": float("nan")}):
            with self.assertRaises(ValueError):
                self.database.update_checkpoint("REQ-1", applied_solution=value)

    def test_atomic_success_and_failure_rollback(self):
        self.database.create_checkpoint(WorkflowCheckpoint(request_id="REQ-1"))
        self.database.update_workflow("REQ-1", status=RequestStatus.PROCESSING,
                                      stage=WorkflowStage.DESIGNING, candidate_pool=self.pool())
        before_request = self.database.get_request("REQ-1")
        before_checkpoint = self.database.get_checkpoint("REQ-1")
        self.assertEqual(before_checkpoint.candidate_pool, self.pool())
        self.assertEqual(before_request.stage, WorkflowStage.DESIGNING)
        self.assertGreater(before_request.updated_at, self.request.updated_at)
        with closing(self.database._connect()) as connection, connection:
            connection.execute("""CREATE TRIGGER fail_progress AFTER UPDATE ON requests
                                  BEGIN SELECT RAISE(ABORT, 'forced failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.update_workflow("REQ-1", status=RequestStatus.COMPLETED,
                                          stage=WorkflowStage.COMPLETED, candidate_pool=None)
        self.assertEqual(self.database.get_request("REQ-1"), before_request)
        self.assertEqual(self.database.get_checkpoint("REQ-1"), before_checkpoint)


class WorkflowMigrationTests(unittest.TestCase):
    @staticmethod
    def make_v12_fixture(connection):
        """Model the pre-v13 request schema while retaining all copied history."""
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("BEGIN IMMEDIATE")
        for trigger in ("validate_request_business_status_insert", "validate_request_business_status_update",
                        "validate_request_customer_identity_insert", "validate_request_customer_identity_update",
                        "protect_order_history_fields", "protect_order_history_delete"):
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
        columns = (
            "request_id", "customer_id", "requirements_json", "status", "stage", "created_at", "updated_at",
            "customer_name", "business_status", "usage_level_json", "budget", "recommended_solution_ids_json",
            "selected_solution_id", "order_id", "previous_package_ids_json", "previous_package_names_json",
            "previous_addon_ids_json", "previous_addon_names_json", "new_package_ids_json", "new_package_names_json",
            "new_addon_ids_json", "new_addon_names_json", "total_price", "error_message", "completed_at",
            "previous_total_price", "previous_plan_json", "new_plan_json",
        )
        projection = ",".join(f'"{column}"' for column in columns)
        connection.execute("""CREATE TABLE requests_v12 (
            request_id TEXT PRIMARY KEY NOT NULL, customer_id TEXT NOT NULL, requirements_json TEXT NOT NULL,
            status TEXT NOT NULL, stage TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            customer_name TEXT, business_status TEXT NOT NULL DEFAULT 'STARTED', usage_level_json TEXT,
            budget NUMERIC, recommended_solution_ids_json TEXT NOT NULL DEFAULT '[]', selected_solution_id TEXT,
            order_id TEXT, previous_package_ids_json TEXT NOT NULL DEFAULT '[]',
            previous_package_names_json TEXT NOT NULL DEFAULT '[]', previous_addon_ids_json TEXT NOT NULL DEFAULT '[]',
            previous_addon_names_json TEXT NOT NULL DEFAULT '[]', new_package_ids_json TEXT NOT NULL DEFAULT '[]',
            new_package_names_json TEXT NOT NULL DEFAULT '[]', new_addon_ids_json TEXT NOT NULL DEFAULT '[]',
            new_addon_names_json TEXT NOT NULL DEFAULT '[]', total_price NUMERIC, error_message TEXT,
            completed_at TEXT, previous_total_price NUMERIC,
            previous_plan_json TEXT NOT NULL DEFAULT '{}', new_plan_json TEXT NOT NULL DEFAULT '{}'
        )""")
        connection.execute(f"INSERT INTO requests_v12 ({projection}) SELECT {projection} FROM requests")
        connection.execute("DROP TABLE requests")
        connection.execute("ALTER TABLE requests_v12 RENAME TO requests")
        connection.execute("PRAGMA user_version = 12")
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

    def test_existing_database_preserved_and_migration_repeatable(self):
        # Read the shipped database without initializing or modifying it.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with closing(sqlite3.connect(DEFAULT_DATABASE_PATH.as_uri() + "?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(path)) as target:
                    source.backup(target)
            tables = ("packages", "addons", "customers", "current_plans", "orders", "requests",
                      "workflow_checkpoints", "order_status_history")
            with closing(sqlite3.connect(path)) as connection:
                self.make_v12_fixture(connection)
                before = {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
                          for table in tables}
                legacy_orphan_count = connection.execute("""SELECT count(*) FROM requests r
                    LEFT JOIN customers c ON c.customer_id=r.customer_id WHERE c.customer_id IS NULL""").fetchone()[0]
            for _ in range(2):
                database = SearchDatabase(path)
                with closing(database._connect()) as connection:
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                    for table in tables:
                        rows = [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                        if table == "requests":
                            # v15 appends nullable search_phase and v16 nullable executor_id;
                            # legacy rows stay NULL in both.
                            self.assertEqual([column[1] for column in connection.execute(
                                "PRAGMA table_info(requests)").fetchall()[-2:]], ["search_phase", "executor_id"])
                            self.assertTrue(all(row[-2] is None and row[-1] is None for row in rows))
                            rows = [row[:-2] for row in rows]
                        self.assertEqual(rows, before[table])
                    self.assertEqual({row[2] for row in connection.execute("PRAGMA foreign_key_list(requests)")}, {"customers", "orders"})
                    self.assertEqual(connection.execute("SELECT count(*) FROM orders").fetchone()[0], len(before["orders"]))
                    self.assertIsNotNone(connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='protect_order_history_fields'").fetchone())
                    # Preserve preexisting invalid development identities instead
                    # of deleting or rewriting those historical request rows.
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    legacy_request_violations = [row for row in violations
                                                 if row[0] == "requests" and row[2] == "customers"]
                    self.assertEqual(len(legacy_request_violations), legacy_orphan_count)

    def test_fresh_database_migrates_to_current_schema(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fresh.sqlite3"
            database = SearchDatabase(path)
            with closing(database._connect()) as connection:
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                self.assertEqual({row[2] for row in connection.execute("PRAGMA foreign_key_list(requests)")}, {"customers", "orders"})
                self.assertIn("search_phase", {row[1] for row in connection.execute("PRAGMA table_info(requests)")})

    def test_failed_migration_rolls_back_ddl_and_version(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute("CREATE TABLE workflow_checkpoints (existing TEXT)")
            with self.assertRaises(sqlite3.OperationalError):
                migrate(connection)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 0)
            self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='requests'").fetchone())

    def test_future_version_is_not_downgraded(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
            with self.assertRaises(ValueError):
                migrate(connection)
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)


if __name__ == "__main__":
    unittest.main()
