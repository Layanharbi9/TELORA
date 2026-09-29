"""Basic Design contracts and grounding, driven by a native tool-calling loop."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pydantic import ValidationError
from backend.agents import solution_design_agent as agent
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate, AddonCandidate
from backend.schemas.solution import DesignedSolution, DesignProposal, SolutionStrategy
from backend.services.llm_retry import InvalidStructuredOutput


def ai_message(tool_calls):
    """A minimal stand-in for a bind_tools() AIMessage: only .tool_calls is read."""
    return SimpleNamespace(tool_calls=tool_calls)


def proposal_call(args, call_id="call-design"):
    return {"name": "DesignProposal", "args": args, "id": call_id}


def calc_call(package_id, addon_ids, call_id="call-calc"):
    return {"name": "calculate_solution", "args": {"package_id": package_id, "addon_ids": addon_ids}, "id": call_id}


class BasicDesignTests(unittest.TestCase):
    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.strategy = SolutionStrategy(strategy_id='S1', decision_policy='REQUIREMENT_FIT',
            intent='Protect calling needs',
            focus_priorities=[1], intended_tradeoffs=['Explore cost versus calling capability'])
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id='P1', name='Base', price_sar=80, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 100})], addon_candidates=[AddonCandidate(
            addon_id=key, name=key, price_sar=10, matched_needs=[], match_reasons=[])
            for key in ('A1', 'A2', 'A3')])

    def proposal_args(self, **changes):
        # Shape of the model's own DesignProposal tool-call args -- no strategy echo.
        args = dict(policy_rationale='P1 alone meets the requested calling target within budget.',
                    package_id='P1', addon_ids=[])
        args.update(changes)
        return args

    def expected(self, args):
        # Shape of the final DesignedSolution: Python re-attaches the full original strategy.
        return dict(strategy=self.strategy.model_dump(mode='json'), policy_rationale=args['policy_rationale'],
                    package_id=args['package_id'], addon_ids=args['addon_ids'])

    def run_design(self, *turns, tool_traces=None):
        """Each item in `turns` is the list of tool_calls for one bound-model turn."""
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [ai_message(calls) for calls in turns]
        return model, lambda: agent.create_basic_design(self.requirements, self.pool, self.strategy,
                                                         llm=model, tool_traces=tool_traces)

    def test_zero_one_two_addons_grounded_and_json_serializable(self):
        for ids in ([], ['A1'], ['A2', 'A1']):
            args = self.proposal_args(addon_ids=ids)
            model, run = self.run_design([proposal_call(args)])
            with patch.object(agent, 'calculate_solution', side_effect=AssertionError('No calculation')):
                result = run()
            self.assertEqual(result.model_dump(mode='json'), self.expected(args))
            self.assertEqual(DesignedSolution.model_validate_json(result.model_dump_json()), result)
            bound = model.bind_tools.return_value
            self.assertEqual(bound.invoke.call_count, 1)  # zero calculation calls: one turn suffices

    def test_calculate_solution_tool_is_bound_alongside_designproposal(self):
        model, run = self.run_design([proposal_call(self.proposal_args())])
        run()
        tools = model.bind_tools.call_args.args[0]
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0].name, 'calculate_solution')
        self.assertIs(tools[1], DesignProposal)
        model.bind_tools.assert_called_once()

    def test_context_and_input_preservation(self):
        before = self.pool.model_dump()
        model, run = self.run_design([proposal_call(self.proposal_args())])
        result = run()
        messages = model.bind_tools.return_value.invoke.call_args_list[0].args[0]
        context = json.loads(messages[1].content)
        self.assertEqual(list(context), ['needs', 'preferred_budget_sar', 'decision_policy',
                                         'focus_priorities', 'candidate_pool'])
        self.assertEqual(context['preferred_budget_sar'], self.requirements.preferred_budget_sar)
        self.assertEqual(context['candidate_pool'], self.pool.model_dump(mode='json'))
        self.assertEqual(context['decision_policy'], self.strategy.decision_policy)
        self.assertEqual(context['focus_priorities'], self.strategy.focus_priorities)
        result.strategy.intent = 'Changed result'
        self.assertEqual(self.strategy.intent, 'Protect calling needs')
        self.assertEqual(self.pool.model_dump(), before)

    def test_full_original_strategy_preserved_despite_narrowed_context(self):
        # The model is never shown intent/intended_tradeoffs, yet the full
        # original strategy -- including that free text -- comes back on the
        # result via Python re-attachment, not anything the model returned.
        model, run = self.run_design([proposal_call(self.proposal_args())])
        result = run()
        messages = model.bind_tools.return_value.invoke.call_args_list[0].args[0]
        context = json.loads(messages[1].content)
        for key in ('intent', 'intended_tradeoffs', 'strategy'):
            self.assertNotIn(key, context)
        self.assertEqual(result.strategy, self.strategy)
        self.assertEqual(result.strategy.intent, 'Protect calling needs')
        self.assertEqual(result.strategy.intended_tradeoffs, ['Explore cost versus calling capability'])

    def test_unknown_ids_retry_then_recover(self):
        for changes in ({'package_id': 'unknown'}, {'addon_ids': ['unknown']}):
            bad = self.proposal_args(**changes)
            model, run = self.run_design([proposal_call(bad)], [proposal_call(self.proposal_args())])
            self.assertIsInstance(run(), DesignedSolution)
            self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)

    def test_unknown_ids_exhaust_retry_with_metadata(self):
        for changes in ({'package_id': 'unknown'}, {'addon_ids': ['unknown']}):
            bad = self.proposal_args(**changes)
            model, run = self.run_design([proposal_call(bad)], [proposal_call(bad)])
            with self.assertLogs('backend.services.llm_retry', level='ERROR'), \
                 self.assertRaisesRegex(InvalidStructuredOutput, 'Unknown') as caught:
                run()
            self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
            self.assertEqual(caught.exception.llm_retry['attempts'], 2)
            self.assertEqual(caught.exception.llm_retry['retry_reason'], 'INVALID_SCHEMA')

    def test_ambiguous_catalog_ids_retried_then_terminal(self):
        for field in ('package_candidates', 'addon_candidates'):
            items = getattr(self.pool, field)
            items.append(items[0].model_copy())
            args = self.proposal_args()
            model, run = self.run_design([proposal_call(args)], [proposal_call(args)])
            with self.assertLogs('backend.services.llm_retry', level='ERROR'), \
                 self.assertRaisesRegex(InvalidStructuredOutput, 'Ambiguous'):
                run()
            self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
            items.pop()

    def test_schema_failures_retry_then_recover(self):
        for changes in ({'addon_ids': ['A1', 'A1']}, {'addon_ids': ['A1', 'A2', 'A3']},
                        {'package_id': ''}, {'price_sar': 1}, {'capabilities': {}}):
            bad = self.proposal_args(**changes)
            model, run = self.run_design([proposal_call(bad)], [proposal_call(self.proposal_args())])
            self.assertIsInstance(run(), DesignedSolution)
            self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)

    def test_terminal_schema_failure_has_retry_metadata(self):
        model, run = self.run_design([proposal_call({})], [proposal_call({})])
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), self.assertRaises(ValidationError) as caught:
            run()
        self.assertEqual(caught.exception.llm_retry['attempts'], 2)

    def test_invalid_input_priority_fails_before_llm(self):
        self.strategy.focus_priorities = [2]
        model, run = self.run_design([proposal_call(self.proposal_args())])
        with self.assertRaises(ValueError):
            run()
        model.bind_tools.assert_not_called()

    def test_default_factory_and_nonretryable_failure(self):
        model, _ = self.run_design([proposal_call(self.proposal_args())])
        with patch.object(agent, 'create_design_llm', return_value=model) as factory:
            agent.create_basic_design(self.requirements, self.pool, self.strategy)
            factory.assert_called_once_with()
        error = RuntimeError('client failure')
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [error]
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), self.assertRaises(RuntimeError) as caught:
            agent.create_basic_design(self.requirements, self.pool, self.strategy, llm=model)
        self.assertIs(caught.exception, error)
        self.assertEqual(model.bind_tools.return_value.invoke.call_count, 1)

    # --- Focused native tool-calling behavior ---------------------------------

    def test_zero_calculation_calls_is_valid_and_produces_no_tool_traces(self):
        traces = []
        model, run = self.run_design([proposal_call(self.proposal_args())], tool_traces=traces)
        run()
        self.assertEqual(traces, [])

    def test_single_in_loop_calculation_call_is_traced_and_executed_for_real(self):
        traces = []
        args = self.proposal_args(addon_ids=['A1'])
        model, run = self.run_design(
            [calc_call('P1', ['A1'])],
            [proposal_call(args)],
            tool_traces=traces)
        with patch.object(agent, 'calculate_solution', wraps=agent.calculate_solution) as calculation:
            result = run()
        calculation.assert_called_once_with(self.pool, package_id='P1', addon_ids=['A1'], preferred_budget_sar=100)
        self.assertEqual(result.package_id, 'P1')
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].tool_name, 'calculate_solution:pricing')
        self.assertEqual(traces[0].status, 'SUCCESS')
        self.assertEqual(traces[0].input, {'package_id': 'P1', 'addon_ids': ['A1'], 'preferred_budget_sar': 100})
        self.assertEqual(traces[0].output['total_price_sar'], '90.0')
        # ToolMessage carrying the result back to the model on the next turn.
        # Index by position (not [-1]): call_args_list holds a reference to the
        # same growing message list, which gains the finalizing AI message
        # after this second invoke() call is made.
        second_turn_messages = model.bind_tools.return_value.invoke.call_args_list[1].args[0]
        tool_message = second_turn_messages[3]  # System, Human, AI(calc), Tool
        self.assertEqual(tool_message.tool_call_id, 'call-calc')
        self.assertEqual(json.loads(tool_message.content)['total_price_sar'], '90.0')

    def test_multiple_calculation_calls_in_one_turn_are_all_executed_and_traced(self):
        traces = []
        model, run = self.run_design(
            [calc_call('P1', [], call_id='c1'), calc_call('P1', ['A1'], call_id='c2')],
            [proposal_call(self.proposal_args())],
            tool_traces=traces)
        run()
        self.assertEqual(len(traces), 2)
        self.assertEqual([t.task_index for t in traces], [1, 2])
        second_turn_messages = model.bind_tools.return_value.invoke.call_args_list[1].args[0]
        tool_messages = [m for m in second_turn_messages if getattr(m, 'tool_call_id', None) in ('c1', 'c2')]
        self.assertEqual({m.tool_call_id for m in tool_messages}, {'c1', 'c2'})

    def test_invalid_in_loop_calculation_does_not_abort_and_model_can_recover(self):
        traces = []
        model, run = self.run_design(
            [calc_call('UNKNOWN', [])],
            [proposal_call(self.proposal_args())],
            tool_traces=traces)
        result = run()  # must not raise
        self.assertEqual(result.package_id, 'P1')
        self.assertEqual(traces[0].status, 'ERROR')
        self.assertIn('error', traces[0].output)

    def test_preferred_budget_is_not_an_accepted_tool_argument(self):
        # RC1 isolation: the LLM-facing schema has no budget field at all.
        model, run = self.run_design([proposal_call(self.proposal_args())])
        run()
        tools = model.bind_tools.call_args.args[0]
        calc_tool = tools[0]
        schema_fields = calc_tool.args_schema.model_fields
        self.assertEqual(set(schema_fields), {'package_id', 'addon_ids'})

    def test_exploration_budget_is_bounded_and_ends_in_finalization(self):
        # The model calculates on every turn, including the last one, so the
        # loop never sees a proposal -- finalization must still produce one.
        turns = [[calc_call('P1', [], call_id=f'c{i}')] for i in range(agent.MAX_TOOL_ITERATIONS)]
        traces = []
        model, run = self.run_design(*turns, [proposal_call(self.proposal_args())],
                                     tool_traces=traces)
        result = run()
        self.assertEqual(result.package_id, 'P1')
        self.assertEqual(len(traces), agent.MAX_TOOL_ITERATIONS)
        # MAX_TOOL_ITERATIONS exploration turns plus exactly one finalization call.
        self.assertEqual(model.bind_tools.return_value.invoke.call_count,
                         agent.MAX_TOOL_ITERATIONS + 1)

    def test_text_only_turn_is_tolerated_and_the_model_can_still_finalize(self):
        model, run = self.run_design([], [proposal_call(self.proposal_args())])
        result = run()
        self.assertEqual(result.package_id, 'P1')
        # Two exploration turns, no finalization call and no retry needed.
        self.assertEqual(model.bind_tools.return_value.invoke.call_count, 2)
        model.bind_tools.assert_called_once()

    def test_design_model_is_configured_independently_of_the_other_stages(self):
        # DESIGN is the only stage on DESIGN_MODEL; every other stage stays on
        # OPENAI_MODEL.
        with patch.dict('os.environ', {}, clear=True), \
             patch.object(agent, 'ChatOpenAI') as client:
            agent.create_design_llm()
            self.assertEqual(client.call_args.kwargs['model'], 'gpt-5.6-terra')
            agent.create_solution_design_llm()
            self.assertEqual(client.call_args.kwargs['model'], 'gpt-4o-mini')
        with patch.dict('os.environ', {'DESIGN_MODEL': 'configured'}), \
             patch.object(agent, 'ChatOpenAI') as client:
            agent.create_design_llm()
            self.assertEqual(client.call_args.kwargs['model'], 'configured')
            agent.create_design_llm('explicit')
            self.assertEqual(client.call_args.kwargs['model'], 'explicit')

    def test_design_client_disables_reasoning_for_tool_compatibility(self):
        # Chat Completions rejects function tools for this model family unless
        # reasoning is disabled, and DESIGN is entirely native tool calling.
        with patch.dict('os.environ', {}, clear=True), \
             patch.object(agent, 'ChatOpenAI') as client:
            agent.create_design_llm()
            self.assertEqual(client.call_args.kwargs['reasoning_effort'], 'none')
            # The setting is DESIGN-only; other stages are untouched.
            agent.create_solution_design_llm()
            self.assertNotIn('reasoning_effort', client.call_args.kwargs)

    def test_mixed_turn_executes_calculations_and_defers_the_same_turn_proposal(self):
        # One AIMessage carrying both a calculation and a proposal: the
        # calculation must run and the proposal must NOT be accepted, so the
        # model commits only after seeing what it asked for.
        traces = []
        deferred = self.proposal_args(addon_ids=['A1'])
        accepted = self.proposal_args(addon_ids=['A2'])
        model, run = self.run_design(
            [calc_call('P1', ['A1'], call_id='c1'), proposal_call(deferred, call_id='p1')],
            [proposal_call(accepted, call_id='p2')],
            tool_traces=traces)
        result = run()
        self.assertEqual(result.addon_ids, ['A2'])  # the later proposal, not the deferred one
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].tool_name, 'calculate_solution:pricing')
        self.assertEqual(traces[0].input['addon_ids'], ['A1'])
        # Both tool_calls from the mixed turn were answered, as the API requires.
        second_turn = model.bind_tools.return_value.invoke.call_args_list[1].args[0]
        answered = {m.tool_call_id: m.content for m in second_turn if hasattr(m, 'tool_call_id')}
        self.assertEqual(set(answered), {'c1', 'p1'})
        self.assertIn('deferred', json.loads(answered['p1']))
        self.assertEqual(json.loads(answered['c1'])['total_price_sar'], '90.0')

    def test_design_strategy_also_uses_the_design_model_factory(self):
        # design_strategy resolves the client and passes it down, so it is the
        # call site that actually decides the live DESIGN model.
        model, _ = self.run_design([proposal_call(self.proposal_args())])
        with patch.object(agent, 'create_design_llm', return_value=model) as factory, \
             patch.object(agent, 'create_solution_design_llm') as other:
            agent.design_strategy(self.requirements, self.pool, self.strategy)
            factory.assert_called_once_with()
            other.assert_not_called()


class GuaranteedFinalizationTests(unittest.TestCase):
    """Phase 2: exhausting exploration must not strand a strategy."""

    def setUp(self):
        self.requirements = Requirements(NeedSelection('Calls', 'Medium'), None, None, 100)
        self.strategy = SolutionStrategy(strategy_id='S1', decision_policy='CORE_FOCUS',
            intent='Protect calls', focus_priorities=[1], intended_tradeoffs=[])
        self.pool = CandidatePool(search_status='SUCCESS', package_candidates=[PackageCandidate(
            package_id='P1', name='Base', price_sar=80, matched_needs=[], match_reasons=[],
            capabilities={'voice_minutes': 100})], addon_candidates=[AddonCandidate(
            addon_id='A1', name='A1', price_sar=10, matched_needs=[], match_reasons=[])])
        self.proposal = dict(policy_rationale='P1 protects calls within budget.',
                             package_id='P1', addon_ids=[])

    def run_design(self, *turns, tool_traces=None):
        model = Mock()
        model.bind_tools.return_value.invoke.side_effect = [ai_message(calls) for calls in turns]
        return model, lambda: agent.create_basic_design(self.requirements, self.pool, self.strategy,
                                                        llm=model, tool_traces=tool_traces)

    def exhausting_turns(self, calls):
        return [list(calls) for _ in range(agent.MAX_TOOL_ITERATIONS)]

    def test_repeated_text_only_turns_reach_finalization(self):
        model, run = self.run_design(*self.exhausting_turns([]), [proposal_call(self.proposal)])
        result = run()
        self.assertEqual(result.package_id, 'P1')
        self.assertEqual(model.bind_tools.return_value.invoke.call_count,
                         agent.MAX_TOOL_ITERATIONS + 1)

    def test_finalization_binds_only_designproposal_and_forces_it(self):
        model, run = self.run_design(*self.exhausting_turns([]), [proposal_call(self.proposal)])
        run()
        self.assertEqual(model.bind_tools.call_count, 2)
        exploration, finalization = model.bind_tools.call_args_list
        self.assertEqual([getattr(t, 'name', t) for t in exploration.args[0]],
                         ['calculate_solution', DesignProposal])
        self.assertEqual(exploration.kwargs, {})
        # Finalization: no calculate_solution available, and the proposal forced.
        self.assertEqual(finalization.args[0], [DesignProposal])
        self.assertEqual(finalization.kwargs, {'tool_choice': 'DesignProposal'})

    def test_finalization_receives_the_accumulated_tool_messages(self):
        turns = self.exhausting_turns([calc_call('P1', ['A1'], call_id='c')])
        # Distinct ids so every ToolMessage is retained rather than overwritten.
        for index, turn in enumerate(turns):
            turn[0] = calc_call('P1', ['A1'], call_id=f'c{index}')
        model, run = self.run_design(*turns, [proposal_call(self.proposal)])
        run()
        final_messages = model.bind_tools.return_value.invoke.call_args_list[-1].args[0]
        tool_messages = [m for m in final_messages if hasattr(m, 'tool_call_id')]
        self.assertEqual([m.tool_call_id for m in tool_messages],
                         [f'c{i}' for i in range(agent.MAX_TOOL_ITERATIONS)])
        self.assertEqual(json.loads(tool_messages[0].content)['total_price_sar'], '90.0')
        # The original system + context messages are still the head of the history.
        self.assertEqual(final_messages[0].content, agent.build_design_prompt('CORE_FOCUS'))
        self.assertEqual(json.loads(final_messages[1].content)['decision_policy'], 'CORE_FOCUS')

    def test_finalization_with_unknown_ids_follows_the_existing_retry_contract(self):
        bad = {**self.proposal, 'package_id': 'unknown'}
        attempt = [*self.exhausting_turns([]), [proposal_call(bad)]]
        model, run = self.run_design(*attempt, *attempt)
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), \
             self.assertRaisesRegex(InvalidStructuredOutput, 'Unknown') as caught:
            run()
        self.assertEqual(caught.exception.llm_retry['attempts'], 2)
        self.assertEqual(caught.exception.llm_retry['retry_reason'], 'INVALID_SCHEMA')
        self.assertEqual(model.bind_tools.return_value.invoke.call_count,
                         2 * (agent.MAX_TOOL_ITERATIONS + 1))

    def test_finalization_without_a_proposal_is_an_invalid_structured_output(self):
        attempt = [*self.exhausting_turns([]), []]
        model, run = self.run_design(*attempt, *attempt)
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), \
             self.assertRaisesRegex(InvalidStructuredOutput, 'Forced finalization'):
            run()

    def test_finalization_is_skipped_when_the_model_proposes_in_time(self):
        model, run = self.run_design([proposal_call(self.proposal)])
        run()
        model.bind_tools.assert_called_once()  # no forced finalization binding


if __name__ == '__main__':
    unittest.main()
