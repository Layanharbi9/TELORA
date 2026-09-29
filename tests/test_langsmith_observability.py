import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.database.db import SearchDatabase
from backend.evaluation.datasets import build_dataset_specs
from backend.evaluation.rubrics import (
    APPLY_RUBRICS, SEARCH_RUBRICS, SOLUTION_RUBRICS, WORKFLOW_RUBRICS,
    evaluate_apply_contract, evaluate_search_contract, evaluate_solution_contract,
)
from backend.services import tracing_service
from backend.services.llm_retry import InvalidStructuredOutput, run_llm_operation


class LangSmithTracingPrimitiveTests(unittest.TestCase):
    def setUp(self):
        self.original = os.environ.get("LANGSMITH_TRACING")
        os.environ["LANGSMITH_TRACING"] = "false"

    def tearDown(self):
        if self.original is None:
            os.environ.pop("LANGSMITH_TRACING", None)
        else:
            os.environ["LANGSMITH_TRACING"] = self.original

    def test_trace_span_executes_and_returns_actual_result_without_network(self):
        result = tracing_service.trace_span("unit.operation", lambda: {"value": 7},
                                            inputs={"request_id": "R1"},
                                            metadata={"stage": "TEST"})
        self.assertEqual(result, {"value": 7})

    def test_trace_span_preserves_errors_for_callers(self):
        def fail():
            raise ValueError("expected")
        with self.assertRaisesRegex(ValueError, "expected"):
            tracing_service.trace_span("unit.failure", fail, inputs={"request_id": "R1"})

    def test_metadata_tags_and_project_are_propagated_to_trace_decorator(self):
        captured = {}
        def decorator(**kwargs):
            captured.update(kwargs)
            return lambda fn: fn
        with patch.object(tracing_service, "traceable", side_effect=decorator):
            value = tracing_service.trace_span("unit.metadata", lambda: "ok",
                inputs={"request_id": "R2"}, metadata={"agent": "search-agent"}, tags=("test",))
        self.assertEqual(value, "ok")
        self.assertEqual(captured["project_name"], tracing_service.project_name())
        self.assertIn("telora", captured["tags"])
        self.assertTrue(captured["enabled"] is False)

    def test_sanitizer_removes_secrets_and_chain_of_thought_fields(self):
        safe = tracing_service.sanitize({"api_key": "not-loggable", "reasoning": "private",
                                         "customer_name": "private", "package_name": "Public plan"})
        self.assertEqual(safe["api_key"], "[REDACTED]")
        self.assertNotIn("reasoning", safe)
        self.assertNotIn("customer_name", safe)
        self.assertEqual(safe["package_name"], "Public plan")

    def test_root_child_trace_hierarchy_and_payload_capture(self):
        stack = []
        runs = []
        def fake_traceable(**options):
            def decorate(fn):
                def wrapped(payload, langsmith_extra=None):
                    record = {"name": options["name"], "parent": stack[-1] if stack else None,
                              "inputs": options["process_inputs"](payload),
                              "metadata": langsmith_extra["metadata"]}
                    stack.append(options["name"])
                    try:
                        result = fn(payload)
                        record["outputs"] = options["process_outputs"](result)
                        return result
                    except Exception as error:
                        record["error_type"] = type(error).__name__
                        raise
                    finally:
                        stack.pop()
                        runs.append(record)
                return wrapped
            return decorate
        with patch.object(tracing_service, "traceable", side_effect=fake_traceable):
            result = tracing_service.trace_span(
                "workflow.root",
                lambda: tracing_service.trace_span("search.stage.plan", lambda: {"plan": ["package_search"]},
                    inputs={"request_id": "R3"}, metadata={"stage": "PLAN"}),
                inputs={"request_id": "R3"}, metadata={"workflow_id": "R3"})
        self.assertEqual(result, {"plan": ["package_search"]})
        child = next(run for run in runs if run["name"] == "search.stage.plan")
        self.assertEqual(child["parent"], "workflow.root")
        self.assertEqual(child["outputs"], {"plan": ["package_search"]})

    def test_retry_attempts_create_separate_trace_spans(self):
        calls = []
        attempts = iter((InvalidStructuredOutput("bad schema"), 42))
        def fake_span(name, action, **kwargs):
            calls.append((name, kwargs["inputs"]))
            value = next(attempts)
            if isinstance(value, Exception):
                raise value
            return value
        with patch("backend.services.llm_retry.trace_span", side_effect=fake_span):
            result, metadata = run_llm_operation("ASSESS", lambda: None)
        self.assertEqual(result, 42)
        self.assertEqual(metadata["retry_count"], 1)
        self.assertEqual([input["attempt"] for _, input in calls], [1, 2])


class EvaluationContractTests(unittest.TestCase):
    def test_rubrics_have_explicit_pass_fail_scoring_evidence_and_type(self):
        from backend.evaluation.rubrics import FINAL_RUBRICS
        for section in (SEARCH_RUBRICS, SOLUTION_RUBRICS, APPLY_RUBRICS, WORKFLOW_RUBRICS, FINAL_RUBRICS):
            for metric, rubric in section.items():
                with self.subTest(metric=metric):
                    for field in ("definition", "expected_behavior", "pass_criteria", "fail_criteria",
                                  "scoring_rule", "evidence", "evaluator_type"):
                        self.assertTrue(rubric[field])

    def test_search_contract_checks_ids_and_reassessment_bounds(self):
        outputs = {"candidate_pool": {"package_candidates": [{"package_id": "P1"}],
                                      "addon_candidates": []},
                   "stages": [{"stage": "RETRIEVE"}, {"stage": "JUDGE"}]}
        result = evaluate_search_contract(outputs, {"retrieved_package_ids": ["P1"]})
        self.assertEqual(result["candidate_id_validity"]["score"], 1.0)
        outputs["stages"].extend([{"stage": "REASSESS"}, {"stage": "REASSESS"}])
        result = evaluate_search_contract(outputs, {"retrieved_package_ids": ["P1"]})
        self.assertEqual(result["reassessment_correctness"]["score"], 0.0)

    def test_solution_contract_checks_catalog_prices_and_required_fields(self):
        solution = {"solution_id": "S1", "package": {"package_id": "P1"}, "addons": [],
                    "total_price_sar": 100, "budget_status": "WITHIN_BUDGET", "recommended": True,
                    "needs_coverage": [], "trade_off": "Data allowance vs price"}
        result = evaluate_solution_contract({"solutions": [solution]},
            {"budget_sar": 120, "package_prices": {"P1": 100}, "addon_prices": {},
             "candidate_package_ids": ["P1"], "candidate_addon_ids": []})
        self.assertTrue(all(item["score"] == 1.0 for item in result))
        solution["total_price_sar"] = 101
        result = evaluate_solution_contract({"solutions": [solution]},
            {"budget_sar": 120, "package_prices": {"P1": 100}, "addon_prices": {},
             "candidate_package_ids": ["P1"], "candidate_addon_ids": []})
        self.assertEqual(next(item for item in result if item["key"] == "internal_consistency")["score"], 0.0)

    def test_apply_contract_checks_failure_rollback(self):
        plan = {"package_id": "P1"}
        result = evaluate_apply_contract({"status": "FAILED", "success": False, "failure_recorded": True},
            {"activation_succeeds": False, "expected_success": False, "case_id": "apply_failure"},
            {"current_plan": plan}, {"current_plan": plan, "request_status": "FAILED", "order_status": "FAILED"})
        self.assertTrue(all(item["score"] == 1.0 for item in result))
        self.assertEqual({item["key"] for item in result}, {
            "input_validation", "customer_validation", "solution_validation",
            "package_validation", "addon_validation", "price_correctness",
            "previous_plan_preservation", "order_correctness",
            "current_plan_update_correctness", "request_history_correctness",
            "transaction_integrity", "failure_rollback",
        })


class LangSmithDatasetSpecificationTests(unittest.TestCase):
    def test_datasets_are_catalog_derived_and_include_required_scenarios(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "evaluation.sqlite3"
            database = SearchDatabase(path)
            with database._connect() as connection:
                connection.execute("INSERT INTO packages (package_id,package_name,data_gb,monthly_price_sar) VALUES ('P1','Starter',10,100)")
                connection.execute("INSERT INTO packages (package_id,package_name,data_gb,voice_minutes,roaming_data_gb,international_minutes,monthly_price_sar) VALUES ('P2','Pro',100,500,5,100,200)")
                connection.execute("INSERT INTO customers (customer_id,name,current_package_id) VALUES ('C1','Test Customer','P1')")
            specs = build_dataset_specs(path)
            from backend.schemas.requirements import NeedSelection, Requirements
            from backend.services.request_service import RequestService
            request = RequestService(database).create_request(
                "REQ-TRACE", "C1", Requirements(NeedSelection("Data", "Medium"), None, None, 100))
            database.record_workflow_trace_root(request.request_id, "run-root", request.request_id)
            self.assertEqual(database.get_workflow_trace_root(request.request_id), "run-root")
        self.assertEqual(set(specs), {"search_agent_eval", "solution_design_eval", "apply_tool_eval", "workflow_eval", "end_to_end_eval"})
        search_cases = {item["metadata"]["case_id"] for item in specs["search_agent_eval"]}
        self.assertTrue({"normal_request", "low_budget", "high_data_usage", "high_voice_usage",
                         "roaming_requirement", "international_calling", "multiple_addons",
                         "insufficient_results", "reassessment_required", "no_suitable_candidate"} <= search_cases)
        for dataset in specs.values():
            self.assertTrue(dataset)
            self.assertTrue(all("inputs" in item and "outputs" in item for item in dataset))


if __name__ == "__main__":
    unittest.main()


class TracedActionResilienceTests(unittest.TestCase):
    """A LangSmith outage must never stop a customer action such as Apply."""

    def test_action_runs_when_parent_run_lookup_and_update_fail(self):
        from backend.services import tracing_service

        class BrokenClient:
            def read_run(self, *_args, **_kwargs):
                raise RuntimeError("LangSmith unavailable")

            def update_run(self, *_args, **_kwargs):
                raise RuntimeError("LangSmith unavailable")

        with patch.object(tracing_service, "tracing_enabled", return_value=True), \
                patch("langsmith.Client", BrokenClient), \
                patch.object(tracing_service, "trace_span", side_effect=lambda _name, action, **_kw: action()):
            result = tracing_service.traced_action("customer.selection_apply", lambda: {"status": "ACTIVE"},
                                                   request_id="REQ-1", parent_run_id="run-1")
        self.assertEqual(result, {"status": "ACTIVE"})
