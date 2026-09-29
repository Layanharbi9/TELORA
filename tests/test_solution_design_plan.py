"""PLAN-only tests with deterministic injected models; no network or database."""
import json
import unittest
from unittest.mock import Mock, patch

from pydantic import ValidationError
from backend.agents.solution_design_agent import create_solution_design_llm, create_solution_design_plan
from backend.schemas.requirements import NeedSelection, Requirements
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import SolutionDesignPlan
from backend.services.llm_retry import InvalidStructuredOutput


_DEFAULT_POLICY_CYCLE = ["REQUIREMENT_FIT", "CORE_FOCUS", "CAPABILITY_HEADROOM"]


def plan_with_policies(policies, priorities=None):
    return {"strategies": [dict(strategy_id=f"S{i}", decision_policy=policy,
        intent=f"Explore data coverage option {i}",
        focus_priorities=priorities or [1], intended_tradeoffs=["More data versus monthly cost"])
        for i, policy in enumerate(policies)]}


def plan(count=1, priorities=None):
    # Exactly one REQUIREMENT_FIT (index 0) for count 1-3, matching the real
    # invariant; count 0/4 stay deliberately invalid for the existing
    # invalid-input tests below.
    policies = _DEFAULT_POLICY_CYCLE[:count] if count <= 3 else (
        _DEFAULT_POLICY_CYCLE + ["CORE_FOCUS"] * (count - 3))
    return plan_with_policies(policies, priorities)


class SolutionDesignPlanTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection("Data", "High"), None,
                                         NeedSelection("Calls", "Low"), 200)
        self.pool = CandidatePool(search_status="SUCCESS", package_candidates=[PackageCandidate(
            package_id="P1", name="Data package", price_sar=180, matched_needs=[],
            match_reasons=[], capabilities={"data_gb": 100})], addon_candidates=[AddonCandidate(
            addon_id="A1", name="Calls add-on", price_sar=10, matched_needs=[],
            match_reasons=[], capabilities={"voice_minutes": 100})])

    def fake(self, *outputs):
        model = Mock()
        model.with_structured_output.return_value.invoke.side_effect = outputs
        return model

    def run_plan(self, model):
        return create_solution_design_plan(self.requirements, self.pool, llm=model)

    def test_one_to_three_strategies_and_typed_output(self):
        for count in (1, 2, 3):
            with self.subTest(count=count):
                model = self.fake(plan(count, [1, 3]))
                result = self.run_plan(model)
                self.assertIsInstance(result, SolutionDesignPlan)
                self.assertEqual(len(result.strategies), count)
                self.assertEqual(result.model_dump(mode="json"), plan(count, [1, 3]))
                model.with_structured_output.assert_called_once_with(SolutionDesignPlan, method="function_calling")
        result = self.run_plan(self.fake(SolutionDesignPlan.model_validate(plan())))
        self.assertIsInstance(result, SolutionDesignPlan)

    def test_exact_customer_and_pool_context_without_mutation(self):
        before = self.pool.model_dump(mode="json")
        model = self.fake(plan())
        with patch("backend.agents.solution_design_agent.create_solution_design_llm") as factory:
            self.run_plan(model)
            factory.assert_not_called()
        messages = model.with_structured_output.return_value.invoke.call_args.args[0]
        context = json.loads(messages[1].content)
        self.assertEqual(context["requirements"], self.requirements.to_dict())
        self.assertEqual(context["candidate_pool"], before)
        self.assertEqual(context["needs"], [dict(need="Data", level="High", priority=1),
                                            dict(need="Calls", level="Low", priority=3)])
        self.assertEqual(self.pool.model_dump(mode="json"), before)

    def test_semantic_guidance_reaches_model_for_different_customers(self):
        # These check the instructions delivered to the model, not live compliance.
        customers = [
            Requirements(NeedSelection("Calls", "Medium"), NeedSelection("Roaming", "High"),
                         NeedSelection("Data Sharing", "Low"), 250),
            Requirements(NeedSelection("Multiple Devices", "Low"), None,
                         NeedSelection("International Calls", "High"), 150),
        ]
        for requirements in customers:
            with self.subTest(requirements=requirements):
                model = self.fake(plan())
                create_solution_design_plan(requirements, self.pool, llm=model)
                messages = model.with_structured_output.return_value.invoke.call_args.args[0]
                guidance = " ".join(messages[0].content.split())
                for instruction in (
                    "Level means the desired strength or amount",
                    "Priority means its relative importance",
                    "Respect each requested Level as the target",
                    "Do not automatically maximize a high-priority need",
                    "unnecessarily exceed a Medium/Low target",
                    "Do not intentionally downgrade a requested High level",
                    "Use Priority to decide which requested targets to protect",
                    "First preserve requested Level targets according to Priority",
                    "A lower-priority need must not be improved by intentionally sacrificing the requested target level of a higher-priority need",
                    "whether that target is Low, Medium, or High",
                    "Only use remaining budget or flexibility to strengthen lower-priority needs toward their requested targets",
                    "protect higher-priority needs before lower-priority needs",
                    "do not reduce a higher-priority target merely to improve a lower-priority one",
                    "Apply this rule to intents and intended_tradeoffs",
                    "leave the implementation mechanism undecided",
                    "Whether a need is satisfied by the base package or an add-on belongs to DESIGN",
                    "do not introduce absent needs as objectives, benefits, or sacrifices",
                    "Do not name or commit to a package, add-on, or add-on category",
                    "actual composition belongs to DESIGN",
                    "do not invent usage thresholds",
                    "REQUIREMENT_FIT is the mandatory baseline perspective and must always be",
                    "included, exactly once, regardless of what else applies",
                    "CORE_FOCUS and CAPABILITY_HEADROOM are optional: evaluate each independently",
                    "against the",
                    "actual Requirements and CandidatePool evidence and determine whether it applies to this customer",
                    "Include an optional policy when the CandidatePool contains credible evidence "
                    "that exploring it could produce a meaningfully different composition or",
                    "customer trade-off",
                    "you do not need to prove in advance that the resulting solution will be",
                    "better or will survive later review",
                    "that judgment belongs to later stages, not PLAN",
                    "Select every optional policy you determine applicable, and only those",
                    "an applicable policy is never optional to skip, and an inapplicable policy",
                    "is never included to reach a particular count",
                    "The resulting number of strategies, from one through",
                    "consequence of this evaluation, not a target",
                    "never invent a policy",
                    "outside this library, never combine two policies into one strategy, and",
                    "never include more than one REQUIREMENT_FIT strategy",
                    "REQUIREMENT_FIT is mandatory for every customer",
                    "CORE_FOCUS is applicable when the CandidatePool contains credible evidence of",
                    "an actual trade-off between staying within the customer's preferred budget",
                    "and serving one or more lower-priority needs while preserving higher-priority",
                    "needs",
                    "CAPABILITY_HEADROOM is applicable when the CandidatePool contains credible",
                    "evidence of an actual capability difference beyond the requested Level that",
                    "could justify paying more for it",
                    "a policy's presence in the library is not itself evidence that it applies",
                ):
                    self.assertIn(instruction, guidance)
                context = json.loads(messages[1].content)
                self.assertEqual(context["requirements"], requirements.to_dict())
                self.assertEqual(context["candidate_pool"], self.pool.model_dump(mode="json"))
                requested = [item for item in (requirements.primary_need, requirements.secondary_need,
                                               requirements.third_need) if item is not None]
                self.assertEqual([item["need"] for item in context["needs"]], [item.need for item in requested])
                self.assertEqual([item["level"] for item in context["needs"]], [item.level.value for item in requested])

    def test_requirement_fit_only_is_a_valid_plan(self):
        model = self.fake(plan_with_policies(["REQUIREMENT_FIT"]))
        result = self.run_plan(model)
        self.assertEqual([s.decision_policy for s in result.strategies], ["REQUIREMENT_FIT"])

    def test_requirement_fit_plus_one_optional_policy_is_valid(self):
        for optional in ("CORE_FOCUS", "CAPABILITY_HEADROOM"):
            with self.subTest(optional=optional):
                model = self.fake(plan_with_policies(["REQUIREMENT_FIT", optional]))
                result = self.run_plan(model)
                self.assertEqual([s.decision_policy for s in result.strategies],
                                 ["REQUIREMENT_FIT", optional])

    def test_requirement_fit_plus_both_optional_policies_is_valid(self):
        model = self.fake(plan_with_policies(["REQUIREMENT_FIT", "CORE_FOCUS", "CAPABILITY_HEADROOM"]))
        result = self.run_plan(model)
        self.assertEqual([s.decision_policy for s in result.strategies],
                         ["REQUIREMENT_FIT", "CORE_FOCUS", "CAPABILITY_HEADROOM"])

    def test_plan_missing_requirement_fit_is_retryable_then_fails(self):
        invalid = plan_with_policies(["CORE_FOCUS", "CAPABILITY_HEADROOM"])
        model = self.fake(invalid, invalid)
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), \
                self.assertRaises(InvalidStructuredOutput) as caught:
            self.run_plan(model)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertEqual(caught.exception.llm_retry["retry_reason"], "INVALID_SCHEMA")

    def test_plan_missing_requirement_fit_recovers_on_retry(self):
        invalid = plan_with_policies(["CORE_FOCUS"])
        valid = plan_with_policies(["REQUIREMENT_FIT", "CORE_FOCUS"])
        model = self.fake(invalid, valid)
        result = self.run_plan(model)
        self.assertEqual([s.decision_policy for s in result.strategies], ["REQUIREMENT_FIT", "CORE_FOCUS"])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)

    def test_plan_duplicate_requirement_fit_is_retryable_then_fails(self):
        invalid = plan_with_policies(["REQUIREMENT_FIT", "REQUIREMENT_FIT"])
        model = self.fake(invalid, invalid)
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), \
                self.assertRaises(InvalidStructuredOutput) as caught:
            self.run_plan(model)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertEqual(caught.exception.llm_retry["retry_reason"], "INVALID_SCHEMA")

    def test_invalid_structure_retries_once_then_recovers(self):
        duplicate = plan(2)
        duplicate["strategies"][1]["strategy_id"] = "S0"
        extra = plan()
        extra["strategies"][0]["package_id"] = "P1"
        for invalid in (plan(0), plan(4), duplicate, extra, {"strategies": "invalid"}):
            with self.subTest(invalid=invalid):
                model = self.fake(invalid, plan())
                self.assertEqual(self.run_plan(model).model_dump(), plan())
                calls = model.with_structured_output.return_value.invoke.call_args_list
                self.assertEqual(len(calls), 2)
                self.assertEqual(calls[0], calls[1])

    def test_missing_customer_priority_is_retryable(self):
        model = self.fake(plan(priorities=[2]), plan(priorities=[3]))
        self.assertEqual(self.run_plan(model).strategies[0].focus_priorities, [3])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        model = self.fake(plan(priorities=[2]), plan(priorities=[2]))
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), self.assertRaises(InvalidStructuredOutput) as caught:
            self.run_plan(model)
        self.assertEqual(caught.exception.llm_retry["attempts"], 2)
        self.assertEqual(caught.exception.llm_retry["retry_reason"], "INVALID_SCHEMA")

    def test_invalid_schema_exhausts_shared_retry(self):
        model = self.fake(plan(0), plan(0))
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), self.assertRaises(ValidationError) as caught:
            self.run_plan(model)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertEqual(caught.exception.llm_retry["final_result"], "ERROR")

    def test_nonretryable_error_propagates_unchanged(self):
        error = RuntimeError("client failure")
        model = self.fake(error)
        with self.assertLogs("backend.services.llm_retry", level="ERROR"), self.assertRaises(RuntimeError) as caught:
            self.run_plan(model)
        self.assertIs(caught.exception, error)
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 1)
        self.assertEqual(error.llm_retry["retry_count"], 0)

    def test_default_factory_and_model_override(self):
        with patch.dict("os.environ", {}, clear=True), patch("backend.agents.solution_design_agent.ChatOpenAI") as client:
            create_solution_design_llm()
            client.assert_called_once_with(model="gpt-4o-mini", temperature=0, max_retries=0)
        with patch.dict("os.environ", {"OPENAI_MODEL": "configured"}), patch("backend.agents.solution_design_agent.ChatOpenAI") as client:
            create_solution_design_llm()
            self.assertEqual(client.call_args.kwargs["model"], "configured")
            create_solution_design_llm("explicit")
            self.assertEqual(client.call_args.kwargs["model"], "explicit")
        model = self.fake(plan())
        with patch("backend.agents.solution_design_agent.create_solution_design_llm", return_value=model) as factory:
            create_solution_design_plan(self.requirements, self.pool)
            factory.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
