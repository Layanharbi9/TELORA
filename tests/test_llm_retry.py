import unittest
from unittest.mock import Mock, patch

import httpx
import openai
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage

from backend.agents.search_agent import run_search_agent
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import AssessmentSelection, PackageSearchOutput, SearchJudgeResult, SearchMetadata, SearchPlan
from backend.schemas.state import TELORAState
from backend.services.llm_retry import run_llm_operation


def api_error(cls, status, body=None):
    return cls("sensitive raw details", response=httpx.Response(status, request=httpx.Request("POST", "https://example.test")), body=body)


class TechnicalRetryTests(unittest.TestCase):
    def test_allowlist_retries_once(self):
        request = httpx.Request("POST", "https://example.test")
        errors = [
            (OutputParserException("bad output"), "INVALID_SCHEMA"),
            (openai.APITimeoutError(request), "TIMEOUT"),
            (openai.APIConnectionError(request=request), "CONNECTION"),
            (api_error(openai.RateLimitError, 429, {"code": "rate_limit_exceeded"}), "TEMPORARY_RATE_LIMIT"),
            (api_error(openai.RateLimitError, 429, {"error": {"type": "tokens"}}), "TEMPORARY_RATE_LIMIT"),
        ] + [(api_error(openai.InternalServerError, code), "SERVER_ERROR") for code in (500, 502, 503, 504)]
        for error, category in errors:
            with self.subTest(category=category):
                call = Mock(side_effect=[error, "valid"])
                result, metadata = run_llm_operation("TEST", call)
                self.assertEqual(result, "valid")
                self.assertEqual(call.call_count, 2)
                self.assertEqual(metadata["retry_reason"], category)
                self.assertEqual(metadata["final_result"], "SUCCESS")

    def test_nonretryable_errors_fail_immediately_and_logs_are_sanitized(self):
        errors = [api_error(cls, status) for cls, status in (
            (openai.AuthenticationError, 401), (openai.PermissionDeniedError, 403),
            (openai.NotFoundError, 404), (openai.BadRequestError, 400))]
        errors += [api_error(openai.RateLimitError, 429, body) for body in (
            None, {"code": "credit_balance_exhausted"}, {"error": {"code": "insufficient_quota"}},
            {"code": "credit_balance_exhausted", "type": "rate_limit_error"})]
        errors += [api_error(openai.InternalServerError, 501), ValueError("sensitive raw details")]
        for error in errors:
            with self.subTest(error=type(error).__name__), self.assertLogs("backend.services.llm_retry", level="ERROR") as logs:
                call = Mock(side_effect=error)
                with self.assertRaises(type(error)) as raised:
                    run_llm_operation("JUDGE", call)
                self.assertIs(raised.exception, error)
                self.assertEqual(call.call_count, 1)
                self.assertEqual(error.llm_retry["retry_count"], 0)
                self.assertNotIn("sensitive", str(logs.output))

    def test_second_failure_stops_and_records_final_failure(self):
        call = Mock(side_effect=OutputParserException("sensitive"))
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), self.assertRaises(OutputParserException) as raised:
            run_llm_operation("ASSESS", call)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(raised.exception.llm_retry["attempts"], 2)
        self.assertEqual(raised.exception.llm_retry["final_result"], "ERROR")

    def test_semantic_statuses_do_not_retry(self):
        for status in ("INSUFFICIENT", "NO_RESULTS", "ERROR"):
            call = Mock(return_value=AssessmentSelection(package_ids=[], addon_ids=[], search_status=status))
            _, metadata = run_llm_operation("ASSESS", call)
            call.assert_called_once_with()
            self.assertEqual(metadata["retry_count"], 0)

    def test_each_operation_schema_retry_preserves_routing_and_trace(self):
        # PLAN's own bounded retry is exercised separately below, since it now drives
        # a native tool-calling loop rather than a single with_structured_output call.
        for target in (None, "ASSESS", "JUDGE", "REASSESS"):
            with self.subTest(target=target):
                search, judge_model, assessor, judge = [Mock() for _ in range(4)]
                plan = SearchPlan(tasks=[{"tool_name": "package_search", "need_priorities": [1]}], assessment_focus="Fit")
                selection = AssessmentSelection(package_ids=[], addon_ids=[], search_status="NO_RESULTS")
                decision = SearchJudgeResult(verdict="INSUFFICIENT", gaps=[{
                    "need_priority": 1, "gap_type": "WEAK_COVERAGE", "reason": "Coverage"}], evaluation_summary="Gap")
                judge.invoke.side_effect = [{}, decision] if target == "JUDGE" else [decision]
                assessor.invoke.side_effect = ([{}, selection, selection] if target == "ASSESS" else
                                               [selection, {}, selection] if target == "REASSESS" else [selection, selection])
                search.with_structured_output.side_effect = lambda schema, **kwargs: {AssessmentSelection: assessor}[schema]
                judge_model.with_structured_output.return_value = judge
                with patch("backend.agents.search_agent.plan_and_retrieve", return_value=(plan, {}, [])) as planned:
                    result = run_search_agent(TELORAState("RETRY", Requirements(NeedSelection("Data", "Medium"), None, None, 220)),
                                              llm=search, judge_llm=judge_model)
                planned.assert_called_once()
                self.assertEqual(judge.invoke.call_count, 2 if target == "JUDGE" else 1)
                self.assertEqual(assessor.invoke.call_count, 3 if target in ("ASSESS", "REASSESS") else 2)
                for stage in result.search_trace.stages:
                    if stage.stage in ("PLAN", "ASSESS", "JUDGE", "REASSESS"):
                        meta = stage.details["llm_retry"]
                        self.assertEqual(meta["attempts"], 2 if stage.stage == target else 1)
                        self.assertEqual(meta["final_result"], "SUCCESS")
                self.assertEqual(sum(stage.stage == "REASSESS" for stage in result.search_trace.stages), 1)
                self.assertNotIn("llm_retry", result.candidate_pool.model_dump())
                self.assertTrue(all(call.args[0] is SearchJudgeResult for call in judge_model.with_structured_output.call_args_list))
                self.assertTrue(all(call.args[0] is AssessmentSelection for call in search.with_structured_output.call_args_list))
                # Identical operation inputs are reused on retry, including the model client.
                runner = {"ASSESS": assessor, "JUDGE": judge, "REASSESS": assessor}.get(target)
                if runner is not None:
                    calls = runner.invoke.call_args_list
                    pair = calls[-2:] if target == "REASSESS" else calls[:2]
                    self.assertEqual(pair[0], pair[1])

    def test_plan_native_tool_calling_schema_retry_reruns_whole_operation(self):
        """PLAN's bounded retry reruns the entire native tool-calling loop: on the
        retried attempt the model is given a fresh conversation and may call tools again."""
        search, judge_model, judge = Mock(), Mock(), Mock()
        needs_arg = [{"need": "Data", "level": "Medium", "priority": 1}]
        tool = Mock()
        tool.invoke.return_value = PackageSearchOutput(
            status="NO_RESULTS", candidates=[],
            search_metadata=SearchMetadata(tool_name="package_search", query_summary="Data",
                                           records_considered=0, records_returned=0))
        # Attempt 1: the model stops without covering the only requested need -> invalid.
        # Attempt 2: the model covers it and finishes -> valid.
        tool_model = Mock()
        tool_model.invoke.side_effect = [
            AIMessage(content="I am done.", tool_calls=[]),
            AIMessage(content="", tool_calls=[{"name": "package_search", "args": {
                "needs": needs_arg, "preferred_budget_sar": 220}, "id": "call-1", "type": "tool_call"}]),
            AIMessage(content="Fit", tool_calls=[]),
        ]
        search.bind_tools.return_value = tool_model
        assessor = Mock()
        assessor.invoke.return_value = AssessmentSelection(package_ids=[], addon_ids=[], search_status="NO_RESULTS")
        search.with_structured_output.side_effect = lambda schema, **kwargs: {AssessmentSelection: assessor}[schema]
        judge.invoke.return_value = SearchJudgeResult(verdict="SUFFICIENT", gaps=[], evaluation_summary="Fine")
        judge_model.with_structured_output.return_value = judge
        with patch("backend.agents.search_agent.SEARCH_TOOL_MAP", {"package_search": tool}):
            result = run_search_agent(TELORAState("RETRY-PLAN", Requirements(NeedSelection("Data", "Medium"), None, None, 220)),
                                      llm=search, judge_llm=judge_model)
        self.assertEqual(tool_model.invoke.call_count, 3)
        tool.invoke.assert_called_once()
        plan_stage = next(stage for stage in result.search_trace.stages if stage.stage == "PLAN")
        self.assertEqual(plan_stage.details["llm_retry"]["attempts"], 2)
        self.assertEqual(plan_stage.details["llm_retry"]["final_result"], "SUCCESS")
        self.assertEqual(plan_stage.details["llm_retry"]["retry_reason"], "INVALID_SCHEMA")


if __name__ == "__main__":
    unittest.main()
