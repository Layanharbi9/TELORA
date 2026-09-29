"""Typed Solution Design outputs and observable runtime artifacts.

Callers must check references against original Requirements and CandidatePool.
Needs coverage is a factual, capability-grounded review, not a deterministic
usage threshold -- Level adequacy remains contextual LLM judgment elsewhere.
"""
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from backend.schemas.requirements import NEEDS, NeedLevel
from backend.schemas.search import AddonCandidate, PackageCandidate

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Priority = Annotated[int, Field(strict=True, ge=1, le=3)]

DecisionPolicy = Literal["REQUIREMENT_FIT", "CORE_FOCUS", "CAPABILITY_HEADROOM"]


class _SolutionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CalculatedPricing(_SolutionModel):
    """Exact monetary fields shared by calculation and calculated design."""

    total_price_sar: Decimal = Field(ge=0)
    preferred_budget_sar: Decimal = Field(gt=0)
    budget_difference_sar: Decimal


class CalculationResult(CalculatedPricing):
    """Canonical components and exact monetary values; capabilities stay separate.

    Decimal amounts serialize as strings in JSON to preserve precision.
    """

    package: PackageCandidate
    addons: list[AddonCandidate] = Field(max_length=2)


def _unique(values, label):
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {label} are not allowed")
    return values


class SolutionStrategy(_SolutionModel):
    """`decision_policy` is selected from the fixed TELORA policy library.

    PLAN selects only policies genuinely supported by the actual Requirements
    and CandidatePool; `intent`/`intended_tradeoffs` remain free text so the
    policy stays grounded in this customer's specific context rather than a
    generic label.
    """

    strategy_id: Text
    decision_policy: DecisionPolicy
    intent: Text
    focus_priorities: list[Priority] = Field(min_length=1, max_length=3)
    intended_tradeoffs: list[Text]

    @field_validator("focus_priorities")
    @classmethod
    def unique_priorities(cls, values):
        return _unique(values, "focus priorities")


class CompositionToPrice(_SolutionModel):
    """One package + add-ons composition, priced via the calculate_solution tool.

    This is the args_schema exposed to the LLM as the calculate_solution
    tool's parameters (see backend.agents.solution_design_agent). It carries
    no preferred_budget_sar field: the tool's calling code captures the
    customer's actual preferred budget in a closure, so the model can never
    set or override it.
    """

    package_id: Text
    addon_ids: list[Text] = Field(max_length=2)

    @field_validator("addon_ids")
    @classmethod
    def unique_addons(cls, values):
        return _unique(values, "add-on IDs")


class DesignProposal(_SolutionModel):
    """The LLM's own structured output for one DESIGN call.

    Deliberately excludes `strategy`: `decision_policy` and `focus_priorities`
    (sent separately) are the sole policy signal DESIGN acts on. PLAN's free
    `intent`/`intended_tradeoffs` text is not part of this decision, so the
    model is never asked to echo or reconcile it here.
    """

    policy_rationale: Text
    package_id: Text
    addon_ids: list[Text] = Field(max_length=2)

    @field_validator("addon_ids")
    @classmethod
    def unique_addons(cls, values):
        return _unique(values, "add-on IDs")


class DesignedSolution(_SolutionModel):
    """Basic composition only, before calculation and review.

    `policy_rationale` is observable, visible justification (not hidden
    chain-of-thought): it is requested before `package_id`/`addon_ids` so the
    model states how the supplied `decision_policy` determined the
    composition before committing to it. `strategy` here is the full,
    original PLAN output re-attached by Python for tracing/downstream use --
    it is never sent back to the model as part of `DesignProposal`.
    """

    strategy: SolutionStrategy
    policy_rationale: Text
    package_id: Text
    addon_ids: list[Text] = Field(max_length=2)

    @field_validator("addon_ids")
    @classmethod
    def unique_addons(cls, values):
        return _unique(values, "add-on IDs")


class CalculatedDesign(DesignedSolution, CalculatedPricing):
    """Grounded composition plus tool-produced pricing, without review."""


class NeedCoverage(_SolutionModel):
    """Factual, capability-grounded coverage of one requested need.

    `coverage` is a floor fact (does the selected composition have real
    catalog capability for this need), not a judgment of how well a Level is
    served -- that nuance lives in `evidence`, kept as free text so it stays
    contextual rather than a fixed threshold.
    """

    need: str
    need_priority: Priority
    requested_level: NeedLevel
    coverage: Literal["COVERED", "NOT_COVERED"]
    evidence: Text

    @field_validator("need")
    @classmethod
    def supported_need(cls, value):
        if value not in NEEDS:
            raise ValueError(f"Invalid need: {value}")
        return value


class SolutionReview(_SolutionModel):
    """Factual per-solution review: coverage per need, plus deterministic budget facts.

    No STRONG/MODERATE/WEAK grading and no ACHIEVED/PARTIAL/NOT_ACHIEVED
    strategy classification -- this stage describes what is and isn't
    covered; comparative and priority judgment belongs to COMPARE.

    trade_off_summary is deterministic, Python-authored text built from this
    same needs_coverage after validation -- it states the actual covered vs.
    not-covered outcome, independent of and authoritative over whatever
    DESIGN's free-text policy_rationale claims about the resulting trade-off.
    """

    needs_coverage: list[NeedCoverage] = Field(min_length=1, max_length=3)
    budget_fit: Literal["WITHIN", "ABOVE"]
    budget_summary: Text
    trade_off_summary: Text

    @model_validator(mode="after")
    def unique_needs(self):
        _unique([item.need for item in self.needs_coverage], "needs")
        _unique([item.need_priority for item in self.needs_coverage], "need priorities")
        return self


class CandidateSolution(_SolutionModel):
    """`strategy` is the deterministic representative when strategies converge.

    `also_converged_strategies` lists any other strategies that independently
    reached this identical Package + Add-on composition (empty otherwise). It
    is structured evidence for downstream/UI use, not a display decision.
    """

    strategy: SolutionStrategy
    package_id: Text
    addon_ids: list[Text] = Field(max_length=2)
    total_price_sar: float = Field(ge=0)
    preferred_budget_sar: float = Field(gt=0)
    budget_difference_sar: float
    review: SolutionReview
    also_converged_strategies: list[SolutionStrategy] = Field(default_factory=list)

    @field_validator("addon_ids")
    @classmethod
    def unique_addons(cls, values):
        return _unique(values, "add-on IDs")

    @model_validator(mode="after")
    def consistent_budget_and_focus(self):
        # This tolerance handles floating-point arithmetic, not business budget tolerance.
        difference = Decimal(str(self.total_price_sar)) - Decimal(str(self.preferred_budget_sar))
        if abs(Decimal(str(self.budget_difference_sar)) - difference) > Decimal("0.000000001"):
            raise ValueError("Budget difference must equal total price minus preferred budget")
        if self.review.budget_fit != ("ABOVE" if difference > 0 else "WITHIN"):
            raise ValueError("Budget fit is inconsistent with prices")
        priorities = {item.need_priority for item in self.review.needs_coverage}
        if not set(self.strategy.focus_priorities) <= priorities:
            raise ValueError("Strategy focus must reference reviewed requirements")
        return self


class SolutionComparison(_SolutionModel):
    """Cross-solution trade-off comparison over the valid (guardrail-passing) set.

    Exactly the four dimensions TELORA compares: needs coverage, priority
    fit, budget fit, and the single key trade-off -- never assumed to be
    price, and never a ranking/score.
    """

    needs_coverage_summary: Text
    priority_fit_summary: Text
    budget_fit_summary: Text
    key_tradeoff: Text


class SolutionComparisonDraft(_SolutionModel):
    """LLM comparison shape before Python supplies the authoritative budget text.

    The model's budget placeholder may be blank because it is discarded. Keep
    the other customer-facing comparison fields validated normally.
    """

    needs_coverage_summary: Text
    priority_fit_summary: Text
    budget_fit_summary: str
    key_tradeoff: Text


class CandidateSolutions(_SolutionModel):
    solutions: list[CandidateSolution] = Field(min_length=1, max_length=3)
    # None only when the COMPARE LLM operation failed and the pipeline recovered.
    comparison: SolutionComparison | None

    @model_validator(mode="after")
    def consistent_set(self):
        _unique([item.strategy.strategy_id for item in self.solutions], "strategy IDs")
        first = self.solutions[0]
        def signature(solution):
            return {(item.need, item.need_priority, item.requested_level)
                    for item in solution.review.needs_coverage}
        if any(item.preferred_budget_sar != first.preferred_budget_sar or signature(item) != signature(first)
               for item in self.solutions[1:]):
            raise ValueError("Solutions must review the same requirements and preferred budget")
        return self


class SolutionDesignPlan(_SolutionModel):
    strategies: list[SolutionStrategy] = Field(min_length=1, max_length=3)

    @field_validator("strategies")
    @classmethod
    def unique_strategies(cls, values):
        _unique([item.strategy_id for item in values], "strategy IDs")
        return values


class SolutionRecommendation(_SolutionModel):
    """Select an existing candidate by its unique strategy.strategy_id."""
    recommended_solution_id: Text
    why_recommended: Text = Field(description=(
        "Shown directly to the TELORA customer as 'Recommended Because'. One concise, plain-language "
        "sentence (about 30 words or fewer) that speaks to the customer as 'you'/'your' and states the "
        "concrete, grounded difference that made this the recommendation: versus the other presented "
        "solutions when there are several, or how it fits the customer when it is the only one. Use only "
        "supplied facts (need coverage, total price, difference from preferred budget, budget fit, "
        "package/add-on capability values). If above the preferred budget, state the SAR amount above it "
        "and the concrete benefit gained. Never mention internal IDs or strategy labels, never describe "
        "the customer in the third person, and never explain the reasoning process itself."))


class FinalSolution(_SolutionModel):
    """One UI-facing, final Solution Design entry.

    Assembled deterministically from an already-validated CandidateSolution
    (and, for the recommended entry only, SolutionRecommendation) -- every
    field here is copied from existing grounded results, never recomputed.
    """

    solution_id: Text
    package: PackageCandidate
    addons: list[AddonCandidate] = Field(max_length=2)
    total_price_sar: float = Field(ge=0)
    budget_status: Text
    needs_coverage: list[NeedCoverage] = Field(min_length=1, max_length=3)
    trade_off: Text
    recommended: bool
    recommendation_reason: Text | None = None
    @model_validator(mode="after")
    def reason_only_on_recommended(self):
        if self.recommended and self.recommendation_reason is None:
            raise ValueError("A recommended solution must include recommendation_reason")
        if not self.recommended and self.recommendation_reason is not None:
            raise ValueError("recommendation_reason is only set on the recommended solution")
        return self


class FinalSolutionOutput(_SolutionModel):
    """UI-independent final Solution Design output: one entry per valid solution.

    recommendation_available is False only when RECOMMENDATION could not be
    produced; the valid solutions are still returned and none is recommended.
    Defaults to True so outputs persisted before this field existed read as before.
    """

    solutions: list[FinalSolution] = Field(max_length=3)
    recommendation_available: bool = True

    @model_validator(mode="after")
    def recommended_count_matches_availability(self):
        recommended = [item for item in self.solutions if item.recommended]
        if not self.solutions:
            return self
        if self.recommendation_available and len(recommended) != 1:
            raise ValueError("Exactly one solution must be marked recommended when a recommendation is available")
        if not self.recommendation_available and recommended:
            raise ValueError("No solution may be marked recommended when no recommendation is available")
        return self
