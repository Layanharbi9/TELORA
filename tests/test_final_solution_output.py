"""FINAL OUTPUT assembly: deterministic, no LLM call, no ranking, no recalculation."""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from pydantic import ValidationError

from backend.agents import solution_design_agent as a
from backend.guardrails.solution_guardrails import SolutionGuardrailResult
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import AddonCandidate, CandidatePool, PackageCandidate
from backend.schemas.solution import (
    CandidateSolution, FinalSolution, FinalSolutionOutput, NeedCoverage,
    SolutionComparison, SolutionDesignPlan, SolutionRecommendation, SolutionReview, SolutionStrategy,
)


def strategy(strategy_id):
    return SolutionStrategy(strategy_id=strategy_id, decision_policy="REQUIREMENT_FIT",
                            intent="Protect data", focus_priorities=[1], intended_tradeoffs=[])


def candidate_solution(strategy_id, package_id, addon_ids, total_price_sar, budget_fit, trade_off):
    return CandidateSolution(
        strategy=strategy(strategy_id), package_id=package_id, addon_ids=addon_ids,
        total_price_sar=total_price_sar, preferred_budget_sar=200,
        budget_difference_sar=total_price_sar - 200,
        review=SolutionReview(
            needs_coverage=[NeedCoverage(need="Data", need_priority=1, requested_level="High",
                                         coverage="COVERED", evidence="Data capability present")],
            budget_fit=budget_fit, budget_summary="Budget summary text", trade_off_summary=trade_off))


def guardrail(strategy_id, overall):
    return SolutionGuardrailResult(strategy_id=strategy_id, checks={}, overall=overall,
                                   hard_budget_limit_sar=Decimal("230"))


class AssembleFinalSolutionsTests(unittest.TestCase):
    def setUp(self):
        self.pool = CandidatePool(search_status="SUCCESS", addon_candidates=[
            AddonCandidate(addon_id="A1", name="Roaming Pack", price_sar=20,
                           matched_needs=[], match_reasons=[], capabilities={}),
        ], package_candidates=[
            PackageCandidate(package_id="P1", name="Package One", price_sar=180,
                             matched_needs=[], match_reasons=[], capabilities={"data_gb": 50}),
            PackageCandidate(package_id="P2", name="Package Two", price_sar=220,
                             matched_needs=[], match_reasons=[], capabilities={"data_gb": 80}),
        ])
        self.plan = SolutionDesignPlan(strategies=[dict(
            strategy_id="S1", decision_policy="REQUIREMENT_FIT", intent="x",
            focus_priorities=[1], intended_tradeoffs=[])])
        self.designs = a.MultipleStrategyDesignResult(results=[], failures=[])

    def pipeline_result(self, solutions, guardrails, *, outcome, comparison=None, recommendation=None):
        return a.SolutionPipelineResult(
            plan=self.plan, designs=self.designs, solutions=solutions, guardrails=guardrails,
            outcome=outcome, comparison=comparison, recommendation=recommendation)

    def test_assembles_only_guardrail_passing_solutions_preserving_order(self):
        s1 = candidate_solution("S1", "P1", [], 180, "WITHIN", "S1 trade-off")
        s2 = candidate_solution("S2", "P2", ["A1"], 240, "ABOVE", "S2 trade-off")
        s3 = candidate_solution("S3", "P1", [], 180, "WITHIN", "S3 trade-off")
        result = self.pipeline_result(
            [s1, s2, s3], [guardrail("S1", "PASS"), guardrail("S2", "PASS"), guardrail("S3", "FAIL")],
            outcome="RECOMMENDED",
            comparison=SolutionComparison(needs_coverage_summary="x", priority_fit_summary="x",
                                          budget_fit_summary="x", key_tradeoff="x"),
            recommendation=SolutionRecommendation(recommended_solution_id="S2", why_recommended="Best coverage headroom"))

        output = a.assemble_final_solutions(self.pool, result)

        # S3 failed Guardrails and is excluded; S1/S2 keep their original (unranked) order.
        self.assertEqual([entry.solution_id for entry in output.solutions], ["S1", "S2"])

        first, second = output.solutions
        self.assertEqual(first.package.package_id, "P1")
        self.assertEqual(first.addons, [])
        self.assertEqual(first.total_price_sar, 180)
        # preferred_budget_sar=200 (fixture default), total=180 -> 20.00 SAR under.
        self.assertEqual(first.budget_status, "WITHIN_BUDGET (-20.00 SAR)")
        self.assertEqual(first.needs_coverage, s1.review.needs_coverage)
        self.assertEqual(first.trade_off, "S1 trade-off")
        self.assertFalse(first.recommended)
        self.assertIsNone(first.recommendation_reason)

        self.assertEqual(second.package.package_id, "P2")
        self.assertEqual([addon.addon_id for addon in second.addons], ["A1"])
        self.assertEqual(second.total_price_sar, 240)
        # preferred_budget_sar=200, total=240 -> 40.00 SAR over.
        self.assertEqual(second.budget_status, "ABOVE_BUDGET (+40.00 SAR)")
        self.assertEqual(second.needs_coverage, s2.review.needs_coverage)
        self.assertEqual(second.trade_off, "S2 trade-off")
        self.assertTrue(second.recommended)
        self.assertEqual(second.recommendation_reason, "Best coverage headroom")

    def test_unavailable_recommendation_keeps_every_valid_solution_unrecommended(self):
        s1 = candidate_solution("S1", "P1", [], 180, "WITHIN", "S1 trade-off")
        s2 = candidate_solution("S2", "P2", ["A1"], 240, "ABOVE", "S2 trade-off")
        result = self.pipeline_result([s1, s2], [guardrail("S1", "PASS"), guardrail("S2", "PASS")],
                                      outcome="RECOMMENDED", recommendation=None)
        output = a.assemble_final_solutions(self.pool, result)
        self.assertEqual([entry.solution_id for entry in output.solutions], ["S1", "S2"])
        self.assertFalse(output.recommendation_available)
        self.assertFalse(any(entry.recommended for entry in output.solutions))
        self.assertTrue(all(entry.recommendation_reason is None for entry in output.solutions))

    def test_no_valid_recommendation_yields_empty_output(self):
        s1 = candidate_solution("S1", "P1", [], 180, "WITHIN", "S1 trade-off")
        result = self.pipeline_result([s1], [guardrail("S1", "FAIL")], outcome="NO_VALID_RECOMMENDATION")
        output = a.assemble_final_solutions(self.pool, result)
        self.assertEqual(output.solutions, [])

    def test_package_and_addons_are_resolved_from_the_candidate_pool(self):
        s1 = candidate_solution("S1", "P2", ["A1"], 240, "ABOVE", "S1 trade-off")
        result = self.pipeline_result(
            [s1], [guardrail("S1", "PASS")], outcome="RECOMMENDED",
            comparison=SolutionComparison(needs_coverage_summary="x", priority_fit_summary="x",
                                          budget_fit_summary="x", key_tradeoff="x"),
            recommendation=SolutionRecommendation(recommended_solution_id="S1", why_recommended="Only option"))

        entry = a.assemble_final_solutions(self.pool, result).solutions[0]
        self.assertEqual(entry.package.name, "Package Two")
        self.assertEqual(entry.package.capabilities, {"data_gb": 80})
        self.assertEqual(entry.addons[0].name, "Roaming Pack")
        self.assertEqual(entry.budget_status, "ABOVE_BUDGET (+40.00 SAR)")

    def test_full_pipeline_result_assembles_without_a_second_llm_call(self):
        """Runs the real (mocked-LLM) pipeline, then assembles its actual result:
        confirms assemble_final_solutions matches production shapes, not just hand fixtures."""
        req = Requirements(NeedSelection("Data", "High"), None, None, 200)
        model = Mock()

        def structured(schema, **kwargs):
            runnable = Mock()
            def invoke(messages):
                data = json.loads(messages[1].content)
                if schema is SolutionDesignPlan:
                    return dict(strategies=[dict(strategy_id="S1", decision_policy="REQUIREMENT_FIT",
                                                 intent="Protect data", focus_priorities=[1], intended_tradeoffs=[])])
                if schema is SolutionRecommendation:
                    return dict(recommended_solution_id=data["selectable_solution_ids"][0],
                                why_recommended="Only available option")
                raise AssertionError("Unexpected stage " + schema.__name__)
            runnable.invoke.side_effect = invoke
            return runnable
        model.with_structured_output.side_effect = structured

        def design_turn(messages):
            return SimpleNamespace(tool_calls=[{"name": "DesignProposal", "args": dict(
                policy_rationale="Chosen package meets the data target.", package_id="P1", addon_ids=[]),
                "id": "call-1"}])
        model.bind_tools.return_value.invoke.side_effect = design_turn

        result = a.run_solution_design(req, self.pool, model)
        output = a.assemble_final_solutions(self.pool, result)

        self.assertEqual(len(output.solutions), 1)
        entry = output.solutions[0]
        self.assertEqual(entry.solution_id, "S1")
        self.assertEqual(entry.package.package_id, "P1")
        # req budget=200, P1 price=180 -> 20.00 SAR under.
        self.assertEqual(entry.budget_status, "WITHIN_BUDGET (-20.00 SAR)")
        self.assertTrue(entry.recommended)
        self.assertEqual(entry.recommendation_reason, "Only available option")
        self.assertEqual(entry.trade_off, result.solutions[0].review.trade_off_summary)
        # No SolutionReview call: Quality Check is fully deterministic now.
        self.assertEqual([call.args[0] for call in model.with_structured_output.call_args_list],
                         [SolutionDesignPlan, SolutionRecommendation])


class BudgetStatusFormattingTests(unittest.TestCase):
    def test_within_budget_matches_required_format(self):
        # total=158.25, preferred=300 -> 141.75 SAR under.
        self.assertEqual(
            a._format_budget_status("WITHIN", preferred_budget_sar=300, total_price_sar=158.25),
            "WITHIN_BUDGET (-141.75 SAR)")

    def test_above_budget_matches_required_format(self):
        self.assertEqual(
            a._format_budget_status("ABOVE", preferred_budget_sar=200, total_price_sar=210),
            "ABOVE_BUDGET (+10.00 SAR)")

    def test_amount_is_rounded_to_two_decimal_places(self):
        self.assertEqual(
            a._format_budget_status("ABOVE", preferred_budget_sar=100, total_price_sar=133.333),
            "ABOVE_BUDGET (+33.33 SAR)")


class FinalSolutionSchemaTests(unittest.TestCase):
    def entry(self, **overrides):
        base = dict(
            solution_id="S1",
            package=dict(package_id="P1", name="P1", price_sar=100, matched_needs=[], match_reasons=[], capabilities={}),
            addons=[], total_price_sar=100, budget_status="WITHIN_BUDGET (-20.00 SAR)",
            needs_coverage=[dict(need="Data", need_priority=1, requested_level="High", coverage="COVERED", evidence="x")],
            trade_off="x", recommended=False, recommendation_reason=None)
        base.update(overrides)
        return base

    def test_recommendation_reason_required_only_when_recommended(self):
        with self.assertRaises(ValidationError):
            FinalSolution(**self.entry(recommended=True, recommendation_reason=None))
        with self.assertRaises(ValidationError):
            FinalSolution(**self.entry(recommended=False, recommendation_reason="Should not be set"))
        FinalSolution(**self.entry(recommended=True, recommendation_reason="Because"))
        FinalSolution(**self.entry(recommended=False, recommendation_reason=None))

    def test_output_requires_exactly_one_recommended_when_solutions_present(self):
        recommended = self.entry(recommended=True, recommendation_reason="Because")
        other = self.entry(solution_id="S2", recommended=False, recommendation_reason=None)
        FinalSolutionOutput(solutions=[recommended, other])

        with self.assertRaises(ValidationError):
            FinalSolutionOutput(solutions=[other, {**other, "solution_id": "S3"}])
        with self.assertRaises(ValidationError):
            FinalSolutionOutput(solutions=[recommended, {**recommended, "solution_id": "S3"}])
        FinalSolutionOutput(solutions=[])

    def test_recommendation_availability_controls_recommended_count(self):
        recommended = self.entry(recommended=True, recommendation_reason="Because")
        other = self.entry(solution_id="S2")
        self.assertTrue(FinalSolutionOutput(solutions=[recommended, other]).recommendation_available)
        with self.assertRaises(ValidationError):
            FinalSolutionOutput(solutions=[other], recommendation_available=True)
        unavailable = FinalSolutionOutput(solutions=[self.entry(), other], recommendation_available=False)
        self.assertFalse(any(item.recommended for item in unavailable.solutions))
        with self.assertRaises(ValidationError):
            FinalSolutionOutput(solutions=[recommended, other], recommendation_available=False)
        FinalSolutionOutput(solutions=[], recommendation_available=False)

    def test_output_persisted_before_the_field_existed_still_validates(self):
        legacy = {"solutions": [self.entry(recommended=True, recommendation_reason="Because")]}
        self.assertTrue(FinalSolutionOutput.model_validate(legacy).recommendation_available)


if __name__ == "__main__":
    unittest.main()
