"""Regression checks for retirement of standalone Search persistence."""

import inspect
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from backend.agents import search_agent
from backend.database.db import SearchDatabase
from backend.database.migrations import migrate
from backend.database.migrations import SCHEMA_VERSION
from backend.database.seed_data import seed_data
from backend.schemas.requirements import NeedSelection, Requirements
from backend.services.request_service import RequestService


class LegacyCleanupTests(unittest.TestCase):
    def test_v1_cleanup_preserves_workflow_records_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cleanup.sqlite3"
            database = SearchDatabase(path)
            seed_data(database_path=path)
            service = RequestService(database)
            service.create_request("REQ-KEEP", "CUST-01", Requirements(NeedSelection("Data", "Medium"), None, None, 200))
            database.update_checkpoint("REQ-KEEP", selected_solution={"preserve": True})
            with closing(database._connect()) as connection, connection:
                connection.execute("CREATE TABLE search_requests (request_id TEXT PRIMARY KEY, snapshot TEXT)")
                connection.executemany("INSERT INTO search_requests VALUES (?, ?)", [(str(i), "development") for i in range(6)])
                connection.execute("PRAGMA user_version = 1")
            before = (service.get_request("REQ-KEEP"), service.get_checkpoint("REQ-KEEP"))
            for _ in range(2):
                with closing(database._connect()) as connection:
                    migrate(connection)
                    self.assertIsNone(connection.execute("SELECT name FROM sqlite_master WHERE name='search_requests'").fetchone())
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                self.assertEqual((service.get_request("REQ-KEEP"), service.get_checkpoint("REQ-KEEP")), before)

    def test_agent_has_no_persistence_interface_or_import(self):
        source = inspect.getsource(search_agent)
        self.assertNotIn("backend.database", source)
        for retired in ("search_requests", "save_search_state", "persist_legacy"):
            self.assertNotIn(retired, source)
        self.assertNotIn("database", inspect.signature(search_agent.run_search_agent).parameters)
        self.assertFalse(hasattr(SearchDatabase, "save_search_state"))
        self.assertFalse(hasattr(SearchDatabase, "get_search_state"))

    def test_terminal_capture_creates_request_without_file_handoff(self):
        from backend import main
        from backend.schemas.requirements import RequirementSubmission
        requirements = Requirements(NeedSelection("Data", "Medium"), None, None, 200)
        service = Mock()
        service.start_customer_request.return_value.request_id = "REQ-GENERATED"
        with patch("sys.argv", ["backend.main"]), \
             patch.object(main, "collect_submission", return_value=RequirementSubmission("Name", "CUST-01", requirements)), \
             patch.object(main, "RequestService", return_value=service), \
             patch.object(main, "continue_request"), patch("builtins.print"):
            main.main()
        service.start_customer_request.assert_called_once()
        call_args, call_kwargs = service.start_customer_request.call_args
        self.assertEqual(call_args, ("CUST-01", requirements))
        self.assertRegex(call_kwargs["request_id"], r"^REQ-[A-F0-9]{32}$")
        self.assertFalse((Path(__file__).resolve().parents[1] / "data" / "user_requirements.json").exists())

    def test_search_terminal_uses_request_id(self):
        from backend import search_main
        result = Mock(search_trace=None)
        result.state.model_dump.return_value = {"request_id": "REQ-1"}
        with patch("sys.argv", ["search_main", "REQ-1"]), \
             patch.object(search_main, "RequestService") as service, \
             patch.object(search_main, "run_search_for_request", return_value=result) as run, patch("builtins.print"):
            search_main.main()
        run.assert_called_once_with("REQ-1")
        service.return_value.database.claim_execution.assert_called_once_with("REQ-1", search_main.CLI_EXECUTOR_ID)
