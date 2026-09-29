"""Native LLM tool for grounded add-on retrieval from the addons SQLite catalog."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from langchain_core.tools import tool
from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase

from backend.schemas.search import AddonCandidate, AddonSearchInput, AddonSearchOutput, SearchMetadata, SearchNeed, SearchStatus
from backend.tools.search_tool import _as_number, _has_capability


def _search_addons_impl(query: AddonSearchInput, database_path: Path | str = DEFAULT_DATABASE_PATH) -> AddonSearchOutput:
    start = perf_counter()
    try:
        records = SearchDatabase(database_path).get_addons()
        candidates: list[AddonCandidate] = []
        for row in records:
            matched = []
            reasons = []
            for need in query.needs:
                is_match, reason = _has_capability(row, need)
                if is_match:
                    matched.append(need)
                    reasons.append(reason)
            if matched:
                candidates.append(
                    AddonCandidate(
                        addon_id=str(row["addon_id"]),
                        name=str(row["addon_name"]),
                        price_sar=_as_number(row["monthly_price_sar"]),
                        matched_needs=matched,
                        match_reasons=reasons,
                        capabilities={
                            "category": row.get("category"),
                            "data_gb": row.get("data_gb"),
                            "social_data_gb": row.get("social_data_gb"),
                            "roaming_data_gb": row.get("roaming_data_gb"),
                            "international_minutes": row.get("international_minutes"),
                            "multi_sim": row.get("multi_sim"),
                            "streaming_benefit": row.get("streaming_benefit"),
                            "gaming_benefit": row.get("gaming_benefit"),
                            "eligible_for": row.get("eligibility"),
                            "min_tenure_months": row.get("min_tenure_months"),
                        },
                    )
                )
        candidates.sort(key=lambda item: (-len(item.matched_needs), item.price_sar, item.addon_id))
        metadata = SearchMetadata(
            tool_name="addon_search",
            query_summary=", ".join(f"{need.need} ({need.level.value}, priority {need.priority})" for need in query.needs),
            records_considered=len(records),
            records_returned=len(candidates),
            execution_time_ms=round((perf_counter() - start) * 1000, 3),
        )
        status = SearchStatus.SUCCESS if candidates else SearchStatus.NO_RESULTS
        return AddonSearchOutput(status=status, candidates=candidates, search_metadata=metadata)
    except Exception as error:
        metadata = SearchMetadata(
            tool_name="addon_search",
            query_summary="add-on retrieval failed",
            records_considered=0,
            records_returned=0,
            execution_time_ms=round((perf_counter() - start) * 1000, 3),
        )
        return AddonSearchOutput(status=SearchStatus.ERROR, candidates=[], search_metadata=metadata, error=str(error))


@tool(args_schema=AddonSearchInput)
def addon_search(needs: list[SearchNeed], preferred_budget_sar: float) -> AddonSearchOutput:
    """Search ONLY telecom add-ons from the actual TELORA addons SQLite catalog."""
    query = AddonSearchInput(needs=needs, preferred_budget_sar=preferred_budget_sar)
    return _search_addons_impl(query)
