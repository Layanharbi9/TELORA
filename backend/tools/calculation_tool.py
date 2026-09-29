"""Ground selected components and calculate prices without making design decisions."""

from decimal import Decimal, InvalidOperation, localcontext

from backend.schemas.search import AddonCandidate, CandidatePool, PackageCandidate
from backend.schemas.solution import CalculationResult


def _amount(value, label: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError(f"Invalid {label}") from error
    if not amount.is_finite() or amount < 0 or (positive and amount == 0):
        raise ValueError(f"Invalid {label}")
    return amount


def _index(candidates, id_field: str):
    records = {}
    for candidate in candidates:
        key = getattr(candidate, id_field)
        if not isinstance(key, str) or not key.strip():
            raise ValueError(f"Invalid {id_field}")
        if key in records:
            raise ValueError(f"Ambiguous duplicate {id_field}: {key}")
        records[key] = candidate
    return records


def resolve_composition(
    candidate_pool: CandidatePool,
    package_id: str,
    addon_ids: list[str],
) -> tuple[PackageCandidate, list[AddonCandidate]]:
    """Resolve canonical components only; no pricing or design decisions."""
    if not isinstance(package_id, str) or not package_id.strip():
        raise ValueError("A package ID is required")
    if not isinstance(addon_ids, list) or len(addon_ids) > 2:
        raise ValueError("Select a list of at most two add-on IDs")
    if any(not isinstance(key, str) or not key.strip() for key in addon_ids):
        raise ValueError("Invalid add-on ID")
    if len(set(addon_ids)) != len(addon_ids):
        raise ValueError("Duplicate add-on IDs")
    packages = _index(candidate_pool.package_candidates, "package_id")
    addons = _index(candidate_pool.addon_candidates, "addon_id")
    if package_id not in packages:
        raise ValueError(f"Unknown package ID: {package_id}")
    if any(key not in addons for key in addon_ids):
        raise ValueError("Unknown add-on ID")
    package = packages[package_id]
    selected_addons = [addons[key] for key in addon_ids]
    return package, selected_addons


def calculate_solution(
    candidate_pool: CandidatePool,
    package_id: str,
    addon_ids: list[str],
    preferred_budget_sar: Decimal | int | float,
) -> CalculationResult:
    """Resolve and price a composition, without capability aggregation or decisions."""
    package, selected_addons = resolve_composition(candidate_pool, package_id, addon_ids)
    prices = [_amount(item.price_sar, "selected component price") for item in [package, *selected_addons]]
    budget = _amount(preferred_budget_sar, "preferred budget", positive=True)
    amounts = [*prices, budget]
    # Enough precision for exact addition/subtraction even for widely spaced magnitudes.
    with localcontext() as context:
        context.prec = max(28, max(x.adjusted() for x in amounts)
                           - min(x.as_tuple().exponent for x in amounts) + 4)
        total = sum(prices, Decimal(0))
        difference = total - budget
    return CalculationResult(
        package=package.model_copy(deep=True),
        addons=[item.model_copy(deep=True) for item in selected_addons],
        total_price_sar=total,
        preferred_budget_sar=budget,
        budget_difference_sar=difference,
    )
