"""Focused Solution Design contract tests; no model calls or database writes."""
import json
import unittest
from pydantic import ValidationError
from backend.schemas.checkpoint import WorkflowCheckpoint
from backend.schemas.requirements import NeedLevel
from backend.schemas.solution import (
    CandidateSolution, CandidateSolutions, NeedCoverage, SolutionReview,
    SolutionDesignPlan,
)


def solution():
    return dict(strategy=dict(strategy_id="S1", decision_policy="REQUIREMENT_FIT",
                              intent="Prioritize data", focus_priorities=[1], intended_tradeoffs=[]),
                package_id="P1", addon_ids=[],
                total_price_sar=180, preferred_budget_sar=200, budget_difference_sar=-20,
                review=dict(needs_coverage=[dict(need="Data", need_priority=1,
                    requested_level="High", coverage="COVERED", evidence="Data capability present")],
                    budget_fit="WITHIN", budget_summary="Below preference",
                    trade_off_summary="Observed trade-off"))


def output(count=1):
    solutions = [solution() for _ in range(count)]
    for index, item in enumerate(solutions):
        item["strategy"]["strategy_id"] = f"S{index}"
    return dict(solutions=solutions, comparison=dict(
        needs_coverage_summary="Each solution covers Data.",
        priority_fit_summary="Primary need protected in every solution.",
        budget_fit_summary="Each solution stays within budget.",
        key_tradeoff="No meaningful difference." if count > 1 else "Only one solution is supplied."))


class SolutionSchemaTests(unittest.TestCase):
    def test_json_roundtrip_and_existing_checkpoint_contract(self):
        model = CandidateSolutions.model_validate(output())
        payload = model.model_dump(mode="json")
        self.assertEqual(CandidateSolutions.model_validate(json.loads(json.dumps(payload))), model)
        checkpoint = WorkflowCheckpoint(request_id="R1", candidate_solutions=payload)
        self.assertEqual(checkpoint.candidate_solutions, payload)
        self.assertIs(model.solutions[0].review.needs_coverage[0].requested_level, NeedLevel.HIGH)
        self.assertEqual(set(payload), {"solutions", "comparison"})

    def test_budget_math_fit_and_finite_values(self):
        for total in (0, 180, 200, 250, 200.1):
            data = solution()
            data.update(total_price_sar=total, budget_difference_sar=total - 200)
            data["review"]["budget_fit"] = "ABOVE" if total > 200 else "WITHIN"
            CandidateSolution.model_validate(data)
        for field, value in (("total_price_sar", -1), ("preferred_budget_sar", 0),
                             ("budget_difference_sar", 10), ("total_price_sar", float("inf")),
                             ("preferred_budget_sar", float("nan")), ("budget_difference_sar", float("inf"))):
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                CandidateSolution.model_validate({**solution(), field: value})
        data = solution()
        data["review"]["budget_fit"] = "ABOVE"
        with self.assertRaises(ValidationError):
            CandidateSolution.model_validate(data)

    def test_composition_and_text_constraints(self):
        for addons in ([], ["A1"], ["A1", "A2"]):
            CandidateSolution.model_validate({**solution(), "addon_ids": addons})
        for change in ({"addon_ids": ["A1", "A1"]}, {"addon_ids": ["A1", "A2", "A3"]},
                       {"package_id": " "}, {"addon_ids": [""]}, {"thoughts": "hidden"}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                CandidateSolution.model_validate({**solution(), **change})

    def test_coverage_contract_and_unique_needs(self):
        coverage = solution()["review"]["needs_coverage"][0]
        for status in ("COVERED", "NOT_COVERED"):
            NeedCoverage.model_validate({**coverage, "coverage": status})
        for change in ({"need": "Unknown"}, {"need_priority": True}, {"need_priority": 4},
                       {"requested_level": "Extreme"}, {"coverage": "PARTIAL"}, {"evidence": " "}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                NeedCoverage.model_validate({**coverage, **change})
        for other in ({**coverage, "need_priority": 2}, {**coverage, "need": "Calls"}):
            review = solution()["review"]
            review["needs_coverage"].append(other)
            with self.assertRaises(ValidationError):
                SolutionReview.model_validate(review)

    def test_plan_counts_unique_ids_and_focus(self):
        for count in (1, 2, 3):
            SolutionDesignPlan(strategies=[item["strategy"] for item in output(count)["solutions"]])
        for strategies in ([], [solution()["strategy"]] * 2, [solution()["strategy"]] * 4):
            with self.assertRaises(ValidationError):
                SolutionDesignPlan(strategies=strategies)
        for priorities in ([], [1, 1], [0], [True]):
            strategy = {**solution()["strategy"], "focus_priorities": priorities}
            with self.assertRaises(ValidationError):
                SolutionDesignPlan(strategies=[strategy])
        data = solution()
        data["strategy"]["focus_priorities"] = [2]
        with self.assertRaises(ValidationError):
            CandidateSolution.model_validate(data)

    def test_invalid_decision_policy_rejected(self):
        for policy in ("BUDGET_TIER", "BALANCED", ""):
            strategy = {**solution()["strategy"], "decision_policy": policy}
            with self.subTest(policy=policy), self.assertRaises(ValidationError):
                SolutionDesignPlan(strategies=[strategy])

    def test_solution_set_consistency(self):
        for count in (1, 2, 3):
            CandidateSolutions.model_validate(output(count))
        invalid = [output(0), output(4)]
        data = output(2)
        data["solutions"][1]["strategy"]["strategy_id"] = "S0"
        invalid.append(data)
        data = output(2)
        data["solutions"][1].update(preferred_budget_sar=210, budget_difference_sar=-30)
        invalid.append(data)
        data = output(2)
        data["solutions"][1]["review"]["needs_coverage"][0]["requested_level"] = "Low"
        invalid.append(data)
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                CandidateSolutions.model_validate(data)

if __name__ == "__main__":
    unittest.main()
