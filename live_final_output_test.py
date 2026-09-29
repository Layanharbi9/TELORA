"""Manual, read-only observational live test for the FINAL OUTPUT assembly.

Reuses the existing Solution Design live-test scenario (same persisted
Request, same read-only DB access pattern as live_solution_test.py), then
additionally calls assemble_final_solutions() and prints the complete
FinalSolutionOutput. Run explicitly; never imported to execute.
"""
import json
import sqlite3
import sys
import traceback
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import datetime
from pathlib import Path


class Tee:
    def __init__(self, console, log):
        self.console, self.log = console, log

    def write(self, text):
        self.log.write(text)
        self.log.flush()
        self.console.write(text)
        self.console.flush()
        return len(text)

    def flush(self):
        self.log.flush()
        self.console.flush()


def run_test():
    from backend.database.db import DEFAULT_DATABASE_PATH
    from backend.database.workflow_persistence import WorkflowPersistence
    from backend.services.request_service import RequestService
    from backend.agents.solution_design_agent import assemble_final_solutions, run_solution_design

    REQUEST_ID = "REQ-F5D07E3F243B4C74945C32C7B2EC9DF4"

    # Reuse production readers without schema initialization or writable connections.
    class ReadOnlyRecords(WorkflowPersistence):
        def _connect(self):
            connection = sqlite3.connect(
                DEFAULT_DATABASE_PATH.resolve().as_uri() + "?mode=ro",
                uri=True,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            return connection

    database = ReadOnlyRecords()

    def snapshot():
        with closing(database._connect()) as connection:
            connection.execute("BEGIN")
            records = {}
            for table in ("requests", "workflow_checkpoints"):
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE request_id = ?", (REQUEST_ID,)
                ).fetchone()
                if row is None:
                    raise RuntimeError(f"Missing {table} record")
                records[table] = dict(row)
            return records

    def encode(value):
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        return repr(value)

    def show(title, value):
        print("\n=== " + title + " ===", flush=True)
        print(json.dumps(value, default=encode, indent=2), flush=True)

    before = None
    try:
        before = snapshot()
        state = RequestService(database=database).reconstruct_shared_state(REQUEST_ID)
        if (state.status.value, state.stage.value) != ("PROCESSING", "DESIGNING"):
            raise RuntimeError("Expected PROCESSING / DESIGNING")
        if state.candidate_pool is None:
            raise RuntimeError("Missing persisted CandidatePool")
        if state.candidate_pool.search_status.value != "SUCCESS":
            raise RuntimeError("Expected successful persisted CandidatePool")

        show("INITIAL CONTEXT", {
            "request_id": REQUEST_ID,
            "status": state.status.value,
            "stage": state.stage.value,
            "preferred_budget_sar": state.requirements.preferred_budget_sar,
            "package_candidates": len(state.candidate_pool.package_candidates),
            "addon_candidates": len(state.candidate_pool.addon_candidates),
        })

        # Real configured LLM; no Search, persistence, or lifecycle calls.
        result = run_solution_design(state.requirements, state.candidate_pool)
        show("OUTCOME", result.outcome)

        # New: deterministic FINAL OUTPUT assembly, no additional LLM call.
        final_output = assemble_final_solutions(state.candidate_pool, result)
        show("FINAL OUTPUT", final_output)
        print(f"\nFinal Output solution count: {len(final_output.solutions)}", flush=True)
        print("Recommended solution_id: " + next(
            (item.solution_id for item in final_output.solutions if item.recommended), "NONE"), flush=True)

    except Exception as error:
        show("ACTUAL EXCEPTION", {
            "type": type(error).__name__,
            "message": str(error),
        })
        traceback.print_exception(error, file=sys.stdout)
    finally:
        after = snapshot()
        print(
            "\nPERSISTED REQUEST/CHECKPOINT UNCHANGED: " + (str(before == after) if before is not None else "UNVERIFIABLE"),
            flush=True,
        )
        print("\nFINAL STATE:")
        print("status = " + after["requests"]["status"])
        print("stage = " + after["requests"]["stage"])


if __name__ == "__main__":
    log_path = Path(__file__).resolve().parent / (
        "telora-final-output-live-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".txt"
    )
    with log_path.open("x", encoding="utf-8") as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            print("LIVE FINAL OUTPUT TEST - START", flush=True)
            print("Output file: " + str(log_path), flush=True)
            try:
                run_test()
            except Exception:
                traceback.print_exc()
            finally:
                print("LIVE FINAL OUTPUT TEST - END", flush=True)
                print("Output file: " + str(log_path), flush=True)
