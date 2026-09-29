"""Four deterministic checks; report findings without changing candidate data."""
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping

from pydantic import BaseModel

from backend.schemas.requirements import Requirements
from backend.schemas.search import CandidatePool
from backend.schemas.solution import CalculationResult, CandidateSolution
from backend.schemas.state import StageTrace
from backend.tools.calculation_tool import calculate_solution

BUDGET_TOLERANCE = Decimal('0.15')


class GuardrailCheck(BaseModel):
    status: Literal['PASS', 'FAIL']
    reason: str | None = None


class SolutionGuardrailResult(BaseModel):
    strategy_id: str
    checks: dict[str, GuardrailCheck]
    overall: Literal['PASS', 'FAIL']
    hard_budget_limit_sar: Decimal
    recalculated_total_price_sar: Decimal | None = None


def _check(reason=None):
    return GuardrailCheck(status='FAIL' if reason else 'PASS', reason=reason)


def check_solution_guardrails(
    requirements: Requirements,
    candidate_pool: CandidatePool,
    solutions: list[CandidateSolution],
    calculations: Mapping[str, CalculationResult],
    *, stage_traces: list[StageTrace] | None = None,
) -> list[SolutionGuardrailResult]:
    """Check each candidate in order against its final calculation evidence.

    Runs on the consolidated candidate list directly (not bundled with a
    comparison) because Guardrails now runs before COMPARE -- a comparison
    over invalid candidates would never be produced. calculations is keyed by
    strategy_id and contains the accepted DESIGN tool observations, not
    rejected revision observations. Missing evidence is FAIL. No
    recommendation is consumed or changed. An unknown total fails closed for
    price/budget checks with an explicit reason rather than guessing a value.
    """
    budget = Decimal(str(requirements.preferred_budget_sar))
    limit = budget * (Decimal(1) + BUDGET_TOLERANCE)
    results = []
    for solution in solutions:
        key = solution.strategy.strategy_id
        package_id, addon_ids = solution.package_id, solution.addon_ids
        valid_shape = (
            isinstance(package_id, str) and bool(package_id.strip())
            and isinstance(addon_ids, list) and len(addon_ids) <= 2
            and all(isinstance(x, str) and bool(x.strip()) for x in addon_ids)
        )
        composition = _check(None if valid_shape and len(set(addon_ids)) == len(addon_ids)
                             else 'Require exactly one package ID and 0–2 distinct add-on IDs.')
        canonical = None
        catalog_reason = None
        if composition.status == 'FAIL':
            catalog_reason = 'Cannot ground an invalid composition.'
        else:
            try:
                canonical = calculate_solution(candidate_pool, package_id, addon_ids, budget)
            except ValueError as error:
                catalog_reason = str(error)
        evidence = calculations.get(key)
        if canonical is not None:
            if evidence is None:
                catalog_reason = 'Missing final canonical calculation evidence.'
            elif (evidence.package != canonical.package or evidence.addons != canonical.addons):
                catalog_reason = 'Selected component facts/prices differ from canonical CandidatePool data.'
        checks = {
            'catalog_validity': _check(catalog_reason),
            'composition_validity': composition,
        }
        if canonical is None:
            checks['price_integrity'] = _check('Cannot recalculate total from valid canonical components.')
            checks['budget_hard_limit'] = _check('Cannot determine hard-budget compliance without a canonical total.')
        else:
            try:
                stored = Decimal(str(solution.total_price_sar))
                price_matches = stored.is_finite() and stored == canonical.total_price_sar
            except (InvalidOperation, ValueError):
                price_matches = False
            checks['price_integrity'] = _check(None if price_matches else
                f'Stored total does not match canonical total {canonical.total_price_sar} SAR.')
            checks['budget_hard_limit'] = _check(None if canonical.total_price_sar <= limit else
                f'Canonical total {canonical.total_price_sar} SAR exceeds hard limit {limit} SAR.')
        result = SolutionGuardrailResult(
            strategy_id=key, checks=checks,
            overall='PASS' if all(c.status == 'PASS' for c in checks.values()) else 'FAIL',
            hard_budget_limit_sar=limit,
            recalculated_total_price_sar=canonical.total_price_sar if canonical else None,
        )
        results.append(result)
        if stage_traces is not None:
            stage_traces.append(StageTrace('GUARDRAILS', result.overall, 0.0, result.model_dump(mode='json')))
    return results
