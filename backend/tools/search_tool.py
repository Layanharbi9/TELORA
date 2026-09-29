"""Native LLM tool for grounded package retrieval from the packages SQLite catalog."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Any

from langchain_core.tools import tool
from backend.database.db import DEFAULT_DATABASE_PATH, SearchDatabase

from backend.schemas.search import (
    PackageCandidate,
    PackageSearchInput,
    PackageSearchOutput,
    SearchMetadata,
    SearchNeed,
    SearchStatus,
)


def _as_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


# Capability fields relevant to each need, for deterministic redundancy comparisons
# (e.g. Pareto dominance in Search's REDUCE stage). Mirrors _has_capability's field
# choice per need without changing its boolean matching logic.
NEED_RELEVANT_FIELDS: dict[str, tuple[str, ...]] = {
    "Data": ("data_gb",),
    "Calls": ("voice_minutes",),
    "Gaming": ("gaming_benefit",),
    "Streaming": ("streaming_benefit",),
    "Social Media": ("social_data_gb",),
    "Roaming": ("roaming_data_gb",),
    "International Calls": ("international_minutes",),
    "Multiple Devices": ("multi_sim",),
    "Data Sharing": ("data_sharing",),
}


def _has_capability(row: dict[str, Any], need: SearchNeed) -> tuple[bool, str]:
    field_rules = {
        "Data": lambda: _as_number(row.get("data_gb")) > 0,
        "Calls": lambda: _as_number(row.get("voice_minutes")) > 0,
        "Gaming": lambda: str(row.get("gaming_benefit") or "None") != "None",
        "Streaming": lambda: str(row.get("streaming_benefit") or "None") != "None",
        "Social Media": lambda: _as_number(row.get("social_data_gb")) > 0 or str(row.get("social_data_gb")) == "Unlimited",
        "Roaming": lambda: _as_number(row.get("roaming_data_gb")) > 0,
        "International Calls": lambda: _as_number(row.get("international_minutes")) > 0,
        "Multiple Devices": lambda: str(row.get("multi_sim")) == "Yes",
        "Data Sharing": lambda: str(row.get("data_sharing")) == "Yes",
    }
    matched = field_rules[need.need]()
    return matched, f"{need.need} capability is present in the catalog record" if matched else ""


def _metadata(start: float, records_considered: int, records_returned: int, query: PackageSearchInput) -> SearchMetadata:
    return SearchMetadata(
        tool_name="package_search",
        query_summary=", ".join(f"{need.need} ({need.level.value}, priority {need.priority})" for need in query.needs),
        records_considered=records_considered,
        records_returned=records_returned,
        execution_time_ms=round((perf_counter() - start) * 1000, 3),
    )


def _search_packages_impl(query: PackageSearchInput, database_path: Path | str = DEFAULT_DATABASE_PATH) -> PackageSearchOutput:
    start = perf_counter()
    try:
        records = SearchDatabase(database_path).get_packages()
        candidates: list[PackageCandidate] = []
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
                    PackageCandidate(
                        package_id=str(row["package_id"]),
                        name=str(row["package_name"]),
                        price_sar=_as_number(row["monthly_price_sar"]),
                        matched_needs=matched,
                        match_reasons=reasons,
                        capabilities={
                            "data_gb": row.get("data_gb"),
                            "social_data_gb": row.get("social_data_gb"),
                            "streaming_benefit": row.get("streaming_benefit"),
                            "gaming_benefit": row.get("gaming_benefit"),
                            "voice_minutes": row.get("voice_minutes"),
                            "roaming_data_gb": row.get("roaming_data_gb"),
                            "international_minutes": row.get("international_minutes"),
                            "multi_sim": row.get("multi_sim"),
                            "data_sharing": row.get("data_sharing"),
                        },
                    )
                )
        candidates.sort(key=lambda item: (-len(item.matched_needs), item.price_sar, item.package_id))
        metadata = _metadata(start, len(records), len(candidates), query)
        status = SearchStatus.SUCCESS if candidates else SearchStatus.NO_RESULTS
        return PackageSearchOutput(status=status, candidates=candidates, search_metadata=metadata)
    except Exception as error:
        metadata = SearchMetadata(
            tool_name="package_search",
            query_summary="package retrieval failed",
            records_considered=0,
            records_returned=0,
            execution_time_ms=round((perf_counter() - start) * 1000, 3),
        )
        return PackageSearchOutput(status=SearchStatus.ERROR, candidates=[], search_metadata=metadata, error=str(error))


@tool(args_schema=PackageSearchInput)
def package_search(needs: list[SearchNeed], preferred_budget_sar: float) -> PackageSearchOutput:
    """Search ONLY telecom packages from the actual TELORA packages SQLite catalog."""
    query = PackageSearchInput(needs=needs, preferred_budget_sar=preferred_budget_sar)
    return _search_packages_impl(query)
