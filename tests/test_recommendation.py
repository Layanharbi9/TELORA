"""Bounded recommendations over existing Quality Check outputs."""
import json
import unittest
from unittest.mock import Mock, patch
from pydantic import ValidationError
from backend.agents import solution_design_agent as a
from backend.schemas.requirements import Requirements, NeedSelection
from backend.schemas.search import CandidatePool, PackageCandidate
from backend.schemas.solution import CandidateSolutions, SolutionRecommendation
from tests.test_solution_schemas import output


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.req=Requirements(NeedSelection('Data','High'),None,None,200)
        self.pool=CandidatePool(search_status='SUCCESS',addon_candidates=[],package_candidates=[PackageCandidate(
            package_id='P1',name='Data 100',price_sar=180,matched_needs=[],match_reasons=[],
            capabilities={'data_gb':100})])
        self.candidates=CandidateSolutions.model_validate(output(2))
        self.valid=dict(recommended_solution_id='S1',why_recommended='Strong primary Data alignment at 180 SAR.')

    def model(self,*responses):
        model=Mock()
        model.with_structured_output.return_value.invoke.side_effect=list(responses)
        return model

    def context(self,model):
        return json.loads(model.with_structured_output.return_value.invoke.call_args.args[0][1].content)

    def test_existing_selection_explanation_and_input_preservation(self):
        model=self.model(self.valid)
        before=self.candidates.model_dump(mode='json')
        traces=[]
        with patch.object(a,'calculate_solution',side_effect=AssertionError('No calculation')), \
             patch.object(a,'design_strategy',side_effect=AssertionError('No design')):
            result=a.recommend_solution(self.req,self.pool,self.candidates,model,stage_traces=traces)
        self.assertEqual(result.model_dump(),self.valid)
        self.assertEqual(self.candidates.model_dump(mode='json'),before)
        context=self.context(model)
        self.assertEqual(context['requirements'],self.req.to_dict())
        self.assertEqual(context['comparison'],before['comparison'])
        self.assertEqual([s.status for s in traces],['STARTED','SUCCESS'])
        self.assertEqual(traces[0].details['solution_ids'],['S0','S1'])
        self.assertEqual(traces[1].details['llm_retry']['attempts'],1)
        self.assertNotIn('rejected_solution_ids',traces[1].details)
        self.assertEqual(SolutionRecommendation.model_validate_json(result.model_dump_json()),result)

    def test_clean_input_contract_has_selectable_ids_and_grounded_facts(self):
        model=self.model(self.valid)
        a.recommend_solution(self.req,self.pool,self.candidates,model)
        context=self.context(model)
        self.assertEqual(set(context),{'requirements','selectable_solution_ids','solutions','comparison'})
        self.assertEqual(context['selectable_solution_ids'],['S0','S1'])
        self.assertEqual([s['solution_id'] for s in context['solutions']],['S0','S1'])
        entry=context['solutions'][0]
        self.assertEqual(set(entry),{'solution_id','decision_policy','package','addons','total_price_sar',
                                     'budget_difference_sar','budget_fit','needs_coverage','trade_off_summary'})
        self.assertEqual(entry['decision_policy'],'REQUIREMENT_FIT')
        self.assertEqual(entry['package'],{'package_id':'P1','name':'Data 100','price_sar':180.0,
                                           'capabilities':{'data_gb':100}})
        self.assertEqual(entry['addons'],[])
        self.assertEqual((entry['total_price_sar'],entry['budget_difference_sar'],entry['budget_fit']),
                         (180.0,-20.0,'WITHIN'))
        self.assertEqual(entry['needs_coverage'],[dict(need='Data',priority=1,level='High',coverage='COVERED',
                                                       evidence='Data capability present')])
        self.assertEqual(entry['trade_off_summary'],'Observed trade-off')

    def test_converged_strategy_ids_are_never_exposed_as_choices(self):
        data=output(2)
        data['solutions'][0]['also_converged_strategies']=[dict(
            strategy_id='S9',decision_policy='CORE_FOCUS',intent='Converged intent',
            focus_priorities=[1],intended_tradeoffs=[])]
        candidates=CandidateSolutions.model_validate(data)
        model=self.model(self.valid)
        a.recommend_solution(self.req,self.pool,candidates,model)
        payload=model.with_structured_output.return_value.invoke.call_args.args[0][1].content
        self.assertNotIn('S9',payload)
        self.assertNotIn('also_converged_strategies',payload)
        self.assertNotIn('Converged intent',payload)
        self.assertEqual(json.loads(payload)['selectable_solution_ids'],['S0','S1'])

    def test_converged_strategy_id_selected_by_model_is_still_rejected(self):
        data=output(2)
        data['solutions'][0]['also_converged_strategies']=[dict(
            strategy_id='S9',decision_policy='CORE_FOCUS',intent='x',focus_priorities=[1],intended_tradeoffs=[])]
        candidates=CandidateSolutions.model_validate(data)
        invalid=dict(self.valid,recommended_solution_id='S9')
        with self.assertLogs('backend.services.llm_retry',level='ERROR'), \
                self.assertRaises(a.InvalidStructuredOutput):
            a.recommend_solution(self.req,self.pool,candidates,self.model(invalid,invalid))

    def test_comparison_unavailable_is_explicit(self):
        data=output(2)
        data['comparison']=None
        candidates=CandidateSolutions.model_validate(data)
        model=self.model(self.valid)
        a.recommend_solution(self.req,self.pool,candidates,model)
        self.assertIsNone(self.context(model)['comparison'])
        guidance=' '.join(model.with_structured_output.return_value.invoke.call_args.args[0][0].content.split())
        self.assertIn('If comparison is null, the Compare stage\'s comparison is unavailable',guidance)
        self.assertIn('do not invent, infer, or claim any comparison or Key Trade-off',guidance)

    def test_single_solution_still_has_grounded_explanation(self):
        candidates=CandidateSolutions.model_validate(output(1))
        response=dict(self.valid,recommended_solution_id='S0')
        model=self.model(response)
        result=a.recommend_solution(self.req,self.pool,candidates,model)
        self.assertEqual(result.recommended_solution_id,'S0')
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count,1)

    def test_unknown_id_is_retried_and_repaired(self):
        model=self.model(dict(self.valid,recommended_solution_id='invented'),self.valid)
        traces=[]
        result=a.recommend_solution(self.req,self.pool,self.candidates,model,stage_traces=traces)
        self.assertEqual(result.recommended_solution_id,'S1')
        self.assertEqual(traces[-1].details['llm_retry']['retry_count'],1)
        self.assertEqual(traces[-1].details['rejected_solution_ids'],['invented'])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count,2)

    def test_unknown_id_exhausts_only_technical_retry_and_is_observable(self):
        invalid=dict(self.valid,recommended_solution_id='invented')
        model=self.model(invalid,invalid)
        with self.assertLogs('backend.services.llm_retry',level='ERROR'), \
                self.assertRaises(a.InvalidStructuredOutput) as caught:
            a.recommend_solution(self.req,self.pool,self.candidates,model)
        self.assertEqual(caught.exception.llm_retry['attempts'],2)
        self.assertIn("rejected 'invented'",str(caught.exception))
        self.assertIn("['S0', 'S1']",str(caught.exception))
        error_trace=caught.exception.recommendation_traces[-1]
        self.assertEqual(error_trace.status,'ERROR')
        self.assertEqual(error_trace.details['rejected_solution_ids'],['invented','invented'])
        self.assertEqual(len(self.candidates.solutions),2)

    def test_why_recommended_leaking_an_internal_id_is_retried_and_repaired(self):
        leaking = dict(self.valid, why_recommended='Solution S1 offers the strongest Data alignment.')
        model = self.model(leaking, self.valid)
        traces = []
        result = a.recommend_solution(self.req, self.pool, self.candidates, model, stage_traces=traces)
        self.assertEqual(result.why_recommended, self.valid['why_recommended'])
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)
        self.assertEqual(traces[-1].details['llm_retry']['retry_count'], 1)

    def test_why_recommended_leaking_an_internal_id_exhausts_retry(self):
        # A concrete regression for the Recommended-Because bug: the model's
        # own rationale must never leak an internal solution_id like "S1".
        leaking = dict(self.valid, why_recommended='Solution S1 offers the strongest Data alignment.')
        model = self.model(leaking, leaking)
        with self.assertLogs('backend.services.llm_retry', level='ERROR'), \
                self.assertRaises(a.InvalidStructuredOutput) as caught:
            a.recommend_solution(self.req, self.pool, self.candidates, model)
        self.assertIn('why_recommended must not reference internal solution IDs', str(caught.exception))
        self.assertEqual(model.with_structured_output.return_value.invoke.call_count, 2)

    def test_empty_candidates_fail_before_model_call(self):
        self.candidates.solutions.clear()
        model=self.model(self.valid)
        with self.assertRaises(ValidationError):
            a.recommend_solution(self.req,self.pool,self.candidates,model)
        model.with_structured_output.assert_not_called()

    def test_schema_forbids_multiple_selection_blank_reason_and_extra_output(self):
        for data in (dict(self.valid,recommended_solution_id=['S0','S1']),
                     dict(self.valid,why_recommended=' '),dict(self.valid,solutions=[])):
            with self.subTest(data=data),self.assertRaises(ValidationError):
                SolutionRecommendation.model_validate(data)

    def test_grounding_instructions_reach_model(self):
        # These check the instructions delivered to the model, not live compliance.
        model=self.model(self.valid)
        a.recommend_solution(self.req,self.pool,self.candidates,model)
        messages=model.with_structured_output.return_value.invoke.call_args.args[0]
        guidance=' '.join(messages[0].content.split())
        for instruction in (
            'recommended_solution_id MUST be exactly one of selectable_solution_ids',
            'No other ID (package, add-on, or anything else) is a valid choice',
            'Needs Coverage', 'Customer Priorities', 'Budget Fit', 'Key Trade-off',
            'Do not invent or infer a customer preference that was not explicitly supplied',
            'do not treat "covering all requested needs" as',
            'itself a customer priority unless Requirements explicitly establishes that',
            'it is not a statement that completeness matters more than budget',
            'state the exact SAR amount above budget',
            'the concrete additional value it provides compared',
            'not a general appeal to completeness',
            'Broader coverage may still be the right recommendation when the actual',
            'trade-off justifies it and the solution remains within Guardrails',
            'Do not invent numeric scores, weights, capabilities, prices, coverage, or new',
            'decision rules',
            'why_recommended is shown directly to the customer',
            'Never reference',
            'solution_id, recommended_solution_id,',
            'or any other internal',
            'identifier or label',
            'refer to solutions only by their real customer-facing',
            'facts (package/add-on names, price, coverage, budget fit)',
            'Write why_recommended for the TELORA customer, not for an analyst',
            'speak to them directly as "you"/"your"',
            'ideally one concise sentence of about 30 words or fewer',
            'State the concrete grounded difference that made this the recommendation',
            'or how it fits this customer when it is the only one',
            'state the SAR amount above the preferred budget and the concrete benefit gained',
            'Do not describe the customer in the third person',
            'do not explain or evaluate your own reasoning',
            'do not add generic marketing filler',
        ):
            self.assertIn(instruction,guidance)

    def test_why_recommended_schema_carries_the_customer_facing_contract(self):
        # Function calling sends this description in the tool schema, so the
        # model sees the audience/voice contract on the field itself.
        description=' '.join(SolutionRecommendation.model_json_schema()['properties']['why_recommended']['description'].split())
        for text in ("Shown directly to the TELORA customer as 'Recommended Because'",
                     "speaks to the customer as 'you'/'your'", 'about 30 words or fewer',
                     'Use only supplied facts', 'Never mention internal IDs or strategy labels',
                     'never describe the customer in the third person'):
            self.assertIn(text,description)
        # Shape is unchanged: still exactly the two required text fields.
        self.assertEqual(set(SolutionRecommendation.model_fields),{'recommended_solution_id','why_recommended'})
        self.assertEqual(SolutionRecommendation.model_json_schema()['required'],['recommended_solution_id','why_recommended'])


if __name__=='__main__':
    unittest.main()
