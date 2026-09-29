"""Shared, UI-independent model for TELORA user requirements."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import math
from typing import Any


NEEDS = (
    "Data",
    "Calls",
    "Gaming",
    "Streaming",
    "Social Media",
    "Roaming",
    "International Calls",
    "Multiple Devices",
    "Data Sharing",
)


class NeedLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass(frozen=True)
class NeedSelection:
    need: str
    level: NeedLevel

    def __post_init__(self) -> None:
        if self.need not in NEEDS:
            raise ValueError(f"Invalid need: {self.need}")
        try:
            object.__setattr__(self, "level", NeedLevel(self.level))
        except ValueError as error:
            raise ValueError(f"Invalid usage level: {self.level}") from error

    def to_dict(self) -> dict[str, str]:
        return {"need": self.need, "level": self.level.value}


@dataclass(frozen=True)
class Requirements:
    primary_need: NeedSelection
    secondary_need: NeedSelection | None
    third_need: NeedSelection | None
    preferred_budget_sar: int | float

    def __post_init__(self) -> None:
        if self.primary_need is None:
            raise ValueError("Primary need is required")
        selections = (self.primary_need, self.secondary_need, self.third_need)
        present = [selection for selection in selections if selection is not None]
        needs = [selection.need for selection in present]
        if len(needs) != len(set(needs)):
            raise ValueError("Duplicate needs are not allowed")
        object.__setattr__(self, "preferred_budget_sar", self.parse_budget(self.preferred_budget_sar))

    @staticmethod
    def parse_budget(value: Any) -> int | float:
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("budget must be numeric and greater than 0")
        try:
            budget = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("budget must be numeric and greater than 0") from error
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("budget must be numeric and greater than 0")
        return int(budget) if budget.is_integer() else budget

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_need": self.primary_need.to_dict(),
            "secondary_need": self.secondary_need.to_dict() if self.secondary_need else None,
            "third_need": self.third_need.to_dict() if self.third_need else None,
            "preferred_budget_sar": self.preferred_budget_sar,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


@dataclass(frozen=True)
class RequirementSubmission:
    username: str
    customer_id: str
    requirements: Requirements

    def __post_init__(self) -> None:
        if not self.username.strip():
            raise ValueError("Username is required")
        if not self.customer_id.strip():
            raise ValueError("Customer ID is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username.strip(),
            "customer_id": self.customer_id.strip(),
            **self.requirements.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)