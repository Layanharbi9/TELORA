"""Pydantic contracts for TELORA search tools and candidate retrieval."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.schemas.requirements import NEEDS, NeedLevel, Requirements


class SearchStatus(str, Enum):
    SUCCESS = "SUCCESS"
    INSUFFICIENT = "INSUFFICIENT"
    NO_RESULTS = "NO_RESULTS"
    ERROR = "ERROR"


class SearchNeed(BaseModel):
    need: str
    level: NeedLevel
    priority: int = Field(ge=1, le=3)

    @field_validator("need")
    @classmethod
    def validate_need(cls, value: str) -> str:
        if value not in NEEDS:
            raise ValueError(f"Invalid need: {value}")
        return value


class SearchTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: Literal["package_search", "addon_search"]
    need_priorities: list[Annotated[int, Field(strict=True, ge=1, le=3)]] = Field(min_length=1)

    @field_validator("need_priorities")
    @classmethod
    def unique_priorities(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("Task priorities must be unique")
        return values


class SearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks: list[SearchTask] = Field(min_length=1, max_length=2)
    assessment_focus: str = Field(min_length=1)

    @field_validator("assessment_focus")
    @classmethod
    def nonblank_focus(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Assessment focus must not be blank")
        return value

    @model_validator(mode="after")
    def unique_tools(self) -> "SearchPlan":
        names = [task.tool_name for task in self.tasks]
        if len(names) != len(set(names)):
            raise ValueError("Each tool may appear only once in a SearchPlan")
        return self

    def validate_requirements(self, requirements: Requirements) -> None:
        expected = {need.priority for need in search_needs_from_requirements(requirements)}
        referenced = {priority for task in self.tasks for priority in task.need_priorities}
        if referenced != expected:
            raise ValueError("Plan must cover all original need priorities and reference no others")


class PackageSearchInput(BaseModel):
    needs: list[SearchNeed] = Field(min_length=1)
    preferred_budget_sar: float = Field(gt=0)


class AddonSearchInput(PackageSearchInput):
    pass


class SearchMetadata(BaseModel):
    tool_name: str
    query_summary: str
    records_considered: int = Field(ge=0)
    records_returned: int = Field(ge=0)
    execution_time_ms: Optional[float] = Field(default=None, ge=0)


class PackageCandidate(BaseModel):
    package_id: str
    name: str
    price_sar: float
    matched_needs: list[SearchNeed]
    match_reasons: list[str]
    capabilities: dict[str, Any] = Field(default_factory=dict)


class AddonCandidate(BaseModel):
    addon_id: str
    name: str
    price_sar: float
    matched_needs: list[SearchNeed]
    match_reasons: list[str]
    capabilities: dict[str, Any] = Field(default_factory=dict)


class PackageSearchOutput(BaseModel):
    status: SearchStatus
    candidates: list[PackageCandidate]
    search_metadata: SearchMetadata
    error: Optional[str] = None


class AddonSearchOutput(BaseModel):
    status: SearchStatus
    candidates: list[AddonCandidate]
    search_metadata: SearchMetadata
    error: Optional[str] = None


class SearchGap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    need_priority: Annotated[int, Field(strict=True, ge=1, le=3)]
    gap_type: Literal["WEAK_COVERAGE", "LIMITED_OPTIONS", "EXCESSIVE_REDUNDANCY"]
    reason: str = Field(min_length=1)

    @field_validator("reason")
    @classmethod
    def nonblank_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gap reason must not be blank")
        return value


class SearchJudgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["SUFFICIENT", "INSUFFICIENT"]
    gaps: list[SearchGap]
    evaluation_summary: str = Field(min_length=1)

    @field_validator("evaluation_summary")
    @classmethod
    def nonblank_summary(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Judge summary must not be blank")
        return value

    @model_validator(mode="after")
    def consistent_verdict(self) -> "SearchJudgeResult":
        if (self.verdict == "SUFFICIENT") != (len(self.gaps) == 0):
            raise ValueError("SUFFICIENT requires no gaps; INSUFFICIENT requires gaps")
        return self

    def validate_requirements(self, requirements: Requirements) -> None:
        priorities = {need.priority for need in search_needs_from_requirements(requirements)}
        if any(gap.need_priority not in priorities for gap in self.gaps):
            raise ValueError("Judge gap references a nonexistent original need priority")


class AssessmentSelection(BaseModel):
    """Internal ordered LLM selection; canonical facts are reconstructed afterward."""

    model_config = ConfigDict(extra="forbid")
    package_ids: list[str]
    addon_ids: list[str]
    search_status: SearchStatus
    search_notes: list[str] = Field(default_factory=list)

    @field_validator("package_ids", "addon_ids")
    @classmethod
    def validate_ids(cls, values: list[str]) -> list[str]:
        if any(not value.strip() for value in values):
            raise ValueError("Selected IDs must not be blank")
        if len(values) != len(set(values)):
            raise ValueError("Duplicate selected IDs are not allowed")
        return values


class CandidatePool(BaseModel):
    """`search_status` is the final, code-computed workflow routing status
    (SUCCESS/NO_RESULTS/ERROR only -- see run_search_agent). `assessment_status`
    preserves the Search LLM's own ASSESS/REASSESS status (which may include
    INSUFFICIENT) for observability; it is advisory and never controls routing.
    None only for pools persisted before this field existed.
    """
    package_candidates: list[PackageCandidate]
    addon_candidates: list[AddonCandidate]
    search_status: SearchStatus
    search_notes: list[str] = Field(default_factory=list)
    assessment_status: SearchStatus | None = None


def search_needs_from_requirements(requirements: Requirements) -> list[SearchNeed]:
    selections = (requirements.primary_need, requirements.secondary_need, requirements.third_need)
    return [
        SearchNeed(need=selection.need, level=selection.level, priority=priority)
        for priority, selection in enumerate(selections, start=1)
        if selection is not None
    ]
