"""Phase 1: DESIGN sees only its own policy doctrine, plus tool-budget guidance."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import SolutionStrategy


# A sentence unique to each policy's own doctrine. Deliberately not the
# boundary clauses that merely NAME another policy (e.g. REQUIREMENT_FIT's
# "...is CORE_FOCUS's trade-off specifically"): naming a boundary is not the
# same as teaching another policy's lens, and those boundaries must survive.
DOCTRINE_MARKERS = {
    'REQUIREMENT_FIT': (
        'Under REQUIREMENT_FIT, meet the requested Needs and Levels while avoiding unnecessary cost',
        'REQUIREMENT_FIT calls for the ordinary default reading',
        'Under REQUIREMENT_FIT, call calculate_solution only if genuinely uncertain',
        'Under REQUIREMENT_FIT specifically, policy_rationale must not cite lower priority',
    ),
    'CORE_FOCUS': (
        'Under CORE_FOCUS, protect higher-priority requested needs first',
        'CORE_FOCUS declines an above-budget composition whose only benefit is covering a lower-priority need',
        'Under CORE_FOCUS, when a lower-priority need can only be served by adding a package or add-on',
        'Under CORE_FOCUS specifically, policy_rationale must say whether covering a lower-priority need',
    ),
    'CAPABILITY_HEADROOM': (
        'Under CAPABILITY_HEADROOM, favor a composition with useful additional capability',
        'CAPABILITY_HEADROOM calls for more willingness to accept the additional cost',
        'Under CAPABILITY_HEADROOM, call calculate_solution for a higher-capability alternative',
    ),
}


def normalize(text):
    return ' '.join(text.split())


class PolicyDoctrineIsolationTests(unittest.TestCase):
    """The built prompt carries exactly one policy's doctrine."""

    def test_every_policy_in_the_library_has_a_doctrine(self):
        self.assertEqual(set(agent.POLICY_DOCTRINE), set(DOCTRINE_MARKERS))

    def test_prompt_contains_only_the_selected_policys_doctrine(self):
        for policy, own_markers in DOCTRINE_MARKERS.items():
            with self.subTest(policy=policy):
                prompt = normalize(agent.build_design_prompt(policy))
                for marker in own_markers:
                    self.assertIn(marker, prompt)
                for other, other_markers in DOCTRINE_MARKERS.items():
                    if other == policy:
                        continue
                    for marker in other_markers:
                        self.assertNotIn(marker, prompt)

    def test_common_guidance_reaches_every_policy(self):
        for policy in DOCTRINE_MARKERS:
            with self.subTest(policy=policy):
                prompt = normalize(agent.build_design_prompt(policy))
                for text in (
                    'Compose a solution for the supplied strategy.',
                    'Design for requirement fit, not maximum capability.',
                    "decision_policy is the lens for this proposal",
                    'Select exactly one base package providing the strongest foundation',
                    "using the calculate_solution tool's authoritative result",
                    'The calculate_solution tool\'s results remain the sole authoritative source',
                ):
                    self.assertIn(text, prompt)

    def test_finalization_contract_reaches_every_policy(self):
        for policy in DOCTRINE_MARKERS:
            with self.subTest(policy=policy):
                prompt = normalize(agent.build_design_prompt(policy))
                for text in (
                    'When you are ready to finalize, call DesignProposal with a concise policy_rationale',
                    'one package_id, and addon_ids from CandidatePool',
                    'Do not return prices, capabilities, or reviews.',
                ):
                    self.assertIn(text, prompt)

    def test_requirement_fit_keeps_its_boundary_against_core_focus(self):
        # The boundary naming CORE_FOCUS is REQUIREMENT_FIT's own instruction
        # -- it is exactly what the model violated -- so it must be preserved.
        prompt = normalize(agent.build_design_prompt('REQUIREMENT_FIT'))
        for text in (
            'REQUIREMENT_FIT must not exclude a requested need merely because it is lower priority',
            "sacrificing a lower-priority need's coverage to protect budget fit is CORE_FOCUS's "
            "trade-off specifically, not REQUIREMENT_FIT's",
            'that reasoning belongs only to CORE_FOCUS',
        ):
            self.assertIn(text, prompt)
        # ...but CORE_FOCUS's actual lens still must not be taught here.
        self.assertNotIn('Under CORE_FOCUS, protect higher-priority requested needs first', prompt)


class ToolBudgetGuidanceTests(unittest.TestCase):
    """Rounds are limited, batching is allowed, and no count is imposed."""

    def test_budget_and_batching_guidance_reaches_every_policy(self):
        for policy in DOCTRINE_MARKERS:
            with self.subTest(policy=policy):
                prompt = normalize(agent.build_design_prompt(policy))
                for text in (
                    'Your calculation rounds are limited, so spend them deliberately.',
                    'request them together in the same turn rather than one per turn',
                    'issuing multiple calculate_solution calls at once is allowed',
                    'Pricing the catalog exhaustively will consume the available rounds',
                    'this is not a search, enumeration, or optimization step',
                ):
                    self.assertIn(text, prompt)

    def test_no_fixed_call_or_composition_count_is_imposed(self):
        for policy in DOCTRINE_MARKERS:
            with self.subTest(policy=policy):
                prompt = normalize(agent.build_design_prompt(policy))
                for text in (
                    'including zero times; there is no target count',
                    'call it zero times and go straight to your final proposal',
                    'Deciding which compositions are worth pricing, and when enough is known '
                    'to finalize, remains entirely your judgment.',
                ):
                    self.assertIn(text, prompt)


class DesignWiringTests(unittest.TestCase):
    """create_basic_design sends the doctrine for the strategy's own policy."""

    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id='P1', name='Base', price_sar=80, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 100})], addon_candidates=[AddonCandidate(
            addon_id='A1', name='A1', price_sar=10, matched_needs=[], match_reasons=[])])

    def strategy(self, policy):
        return SolutionStrategy(strategy_id='S1', decision_policy=policy, intent='Protect calls',
                                focus_priorities=[1], intended_tradeoffs=[])

    def run_for(self, policy):
        model = Mock()
        args = dict(policy_rationale='P1 meets the requested calling target.',
                    package_id='P1', addon_ids=[])
        model.bind_tools.return_value.invoke.return_value = SimpleNamespace(
            tool_calls=[{'name': 'DesignProposal', 'args': args, 'id': 'call-1'}])
        agent.create_basic_design(self.requirements, self.pool, self.strategy(policy), llm=model)
        messages = model.bind_tools.return_value.invoke.call_args.args[0]
        return normalize(messages[0].content), json.loads(messages[1].content)

    def test_each_strategy_receives_only_its_own_doctrine(self):
        for policy, own_markers in DOCTRINE_MARKERS.items():
            with self.subTest(policy=policy):
                prompt, _ = self.run_for(policy)
                self.assertEqual(prompt, normalize(agent.build_design_prompt(policy)))
                for marker in own_markers:
                    self.assertIn(marker, prompt)
                for other, other_markers in DOCTRINE_MARKERS.items():
                    if other != policy:
                        for marker in other_markers:
                            self.assertNotIn(marker, prompt)

    def test_policy_selection_stays_in_the_context_and_rc1_isolation_holds(self):
        # Python selects only the doctrine; the context is unchanged, still
        # carries decision_policy, and still withholds PLAN's free text.
        for policy in DOCTRINE_MARKERS:
            with self.subTest(policy=policy):
                _, context = self.run_for(policy)
                self.assertEqual(context['decision_policy'], policy)
                self.assertEqual(list(context), ['needs', 'preferred_budget_sar', 'decision_policy',
                                                 'focus_priorities', 'candidate_pool'])
                for key in ('intent', 'intended_tradeoffs', 'strategy'):
                    self.assertNotIn(key, context)


if __name__ == '__main__':
    unittest.main()
