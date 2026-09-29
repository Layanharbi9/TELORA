"""Manual, read-only observational live test. Run explicitly; never imported to execute."""
import json
import sqlite3
import sys
import traceback
from contextlib import closing, redirect_stdout, redirect_stderr
from dataclasses import asdict, is_dataclass
from datetime import datetime
from decimal import Decimal
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
    from backend.schemas.search import search_needs_from_requirements
    from backend.agents.solution_design_agent import run_solution_design

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
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, Decimal):
            return str(value)
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

        needs = search_needs_from_requirements(state.requirements)
        actual = [(n.priority, n.need, n.level.value) for n in needs]
        expected = [(1, "Data", "Medium"), (2, "Streaming", "High"), (3, "Gaming", "Low")]
        if actual != expected or state.requirements.preferred_budget_sar != 320:
            raise RuntimeError("Stored requirements differ from the expected customer context")

        show("INITIAL CONTEXT", {
            "request_id": REQUEST_ID,
            "status": state.status.value,
            "stage": state.stage.value,
            "needs": needs,
            "preferred_budget_sar": state.requirements.preferred_budget_sar,
            "candidate_pool_status": state.candidate_pool.search_status,
            "package_candidates": len(state.candidate_pool.package_candidates),
            "addon_candidates": len(state.candidate_pool.addon_candidates),
        })

        # Real configured LLM; no Search, persistence, or lifecycle calls.
        result = run_solution_design(state.requirements, state.candidate_pool)

        show("PLAN - STRATEGIES AND INTENTS", result.plan)
        for item in result.designs.results:
            key = item.design.strategy.strategy_id
            calculations = [t for t in item.trace.tool_traces if t.tool_name == "calculate_solution"]
            show(f"DESIGN {key} - INITIAL COMPOSITION AND CALCULATION", calculations[0])
            show(f"DESIGN {key} - DESIGN_FINAL", item.design)
            show(f"DESIGN {key} - COMPLETE TRACE", item.trace)

        # Strategies whose DESIGN failed are not in designs.results, so surface
        # their evidence explicitly -- reason carries the raised error message.
        for failure in result.designs.failures:
            key = failure.strategy_id
            show(f"DESIGN {key} - FAILURE", {
                "strategy_id": failure.strategy_id,
                "error_type": failure.error_type,
                "reason": failure.reason,
                "in_loop_calculation_calls": len(failure.trace.tool_traces),
                "stages": [f"{s.stage}: {s.status}" for s in failure.trace.stages],
            })
            show(f"DESIGN {key} - FAILURE COMPLETE TRACE", failure.trace)

        for solution in result.solutions:
            show(f"QUALITY CHECK {solution.strategy.strategy_id}", {
                "final_solution": solution,
                "needs_coverage": solution.review.needs_coverage,
                "budget_fit": solution.review.budget_fit,
                "decision_policy": solution.strategy.decision_policy,
                "also_converged_strategies": [s.strategy_id for s in solution.also_converged_strategies],
            })
        show("CONSOLIDATE (converged strategies merged into one solution)", [
            s.details for s in result.stage_traces if s.stage == "CONSOLIDATE"
        ])
        show("OUTCOME", result.outcome)
        show("COMPARE - TRADE-OFFS", result.comparison)
        show("RECOMMENDATION", result.recommendation)
        solutions = {s.strategy.strategy_id: s for s in result.solutions}
        labels = {
            "catalog_validity": "Catalog Validity",
            "composition_validity": "Composition Validity",
            "price_integrity": "Price Integrity",
            "budget_hard_limit": "Budget Hard Limit",
        }
        for guardrail in result.guardrails:
            show(f"GUARDRAILS {guardrail.strategy_id}", {
                **{label: guardrail.checks[key] for key, label in labels.items()},
                "recalculated_total": guardrail.recalculated_total_price_sar,
                "preferred_budget": solutions[guardrail.strategy_id].preferred_budget_sar,
                "hard_budget_limit": guardrail.hard_budget_limit_sar,
                "overall": guardrail.overall,
            })
        show("STAGE TRACES - COMPLETE", result.stage_traces)
        show("STAGE TRACE ORDER", [f"{s.stage}: {s.status}" for s in result.stage_traces])

        def positions(stage):
            return [i for i, s in enumerate(result.stage_traces) if s.stage == stage]

        def strictly_before(a, b):
            return bool(a and b) and max(a) < min(b)

        quality_positions = positions("QUALITY_CHECK")
        consolidate_positions = positions("CONSOLIDATE")
        guardrail_positions = positions("GUARDRAILS")
        compare_positions = positions("COMPARE")
        recommendation_positions = positions("RECOMMENDATION")
        print("QUALITY_CHECK -> CONSOLIDATE: " + str(strictly_before(quality_positions, consolidate_positions)), flush=True)
        print("CONSOLIDATE -> GUARDRAILS: " + str(strictly_before(consolidate_positions, guardrail_positions)), flush=True)
        print("GUARDRAILS -> COMPARE: " + str(strictly_before(guardrail_positions, compare_positions)), flush=True)
        print("COMPARE -> RECOMMENDATION: " + str(strictly_before(compare_positions, recommendation_positions)), flush=True)

    except Exception as error:
        show("ACTUAL EXCEPTION", {
            "type": type(error).__name__,
            "message": str(error),
        })
        traceback.print_exception(error, file=sys.stdout)
        show("AVAILABLE FAILURE EVIDENCE", {
            name: getattr(error, name)
            for name in (
                "failed_strategy_id", "llm_retry", "design_trace",
                "strategy_failures", "review_trace",
                "review_traces", "completed_candidate_solutions",
                "comparison_trace", "recommendation_traces",
            )
            if hasattr(error, name)
        })
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
        "telora-solution-live-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".txt"
    )
    with log_path.open("x", encoding="utf-8") as log:
        with redirect_stdout(Tee(sys.stdout, log)), redirect_stderr(Tee(sys.stderr, log)):
            print("LIVE SOLUTION TEST - START", flush=True)
            print("Output file: " + str(log_path), flush=True)
            try:
                run_test()
            except Exception:
                traceback.print_exc()
            finally:
                print("LIVE SOLUTION TEST - END", flush=True)
                print("Output file: " + str(log_path), flush=True)
