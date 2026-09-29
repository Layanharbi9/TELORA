"""SQLite catalog and Request workflow persistence."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from backend.database.migrations import migrate
from backend.database.workflow_persistence import WorkflowPersistence



DEFAULT_DATABASE_PATH = Path(__file__).resolve().parents[2] / "data" / "telora.sqlite3"

# Fixed identifiers; untyped value columns preserve Excel numbers and text without coercion.
CATALOG_COLUMNS = {
    "packages": (
        "package_id",
        "package_name",
        "provider_style",
        "package_type",
        "target_segment",
        "eligibility",
        "offer_type",
        "data_gb",
        "social_data_gb",
        "streaming_benefit",
        "gaming_benefit",
        "voice_minutes",
        "sms",
        "roaming_data_gb",
        "international_minutes",
        "multi_sim",
        "data_sharing",
        "contract_months",
        "monthly_price_sar",
        "discount_percentage",
        "catalog_role",
        "benchmark_note",
    ),
    "addons": (
        "addon_id",
        "addon_name",
        "category",
        "monthly_price_sar",
        "data_gb",
        "social_data_gb",
        "roaming_data_gb",
        "international_minutes",
        "multi_sim",
        "streaming_benefit",
        "gaming_benefit",
        "compatible_package_ids",
        "incompatible_package_ids",
        "eligibility",
        "min_tenure_months",
        "stacks_with_other_addons",
        "notes",
    ),
    "customers": (
        "customer_id",
        "name",
        "city",
        "segment",
        "eligibility",
        "tenure_months",
        "monthly_budget_sar",
        "current_package_id",
        "current_package_name",
        "current_price_sar",
        "device_lock",
        "requested_addon_id",
        "usage_2026_03",
        "usage_2026_04",
        "usage_2026_05",
        "usage_2026_06",
        "usage_2026_07",
        "usage_2026_08",
        "latest_usage_gb",
        "usage_pattern",
        "missing_month_count",
        "is_test_case",
        "test_case_id",
        "expected_decision",
        "expected_package_id",
        "expected_addon_action",
        "expected_reason",
        "notes",
        "synthetic",
        "calibration",
    ),
}


class SearchDatabase(WorkflowPersistence):
	"""Read seeded catalogs and provide Request workflow persistence."""

	def __init__(self, path: Path | str = DEFAULT_DATABASE_PATH) -> None:
		self.path = Path(path)
		self.path.parent.mkdir(parents=True, exist_ok=True)
		self._create_schema()

	def _connect(self) -> sqlite3.Connection:
		connection = sqlite3.connect(self.path)
		connection.row_factory = sqlite3.Row
		connection.execute("PRAGMA foreign_keys = ON")
		return connection

	def _create_schema(self) -> None:
		with closing(self._connect()) as connection, connection:
			for table, columns in CATALOG_COLUMNS.items():
				definitions = [f'"{columns[0]}" TEXT PRIMARY KEY NOT NULL']
				definitions.extend(f'"{column}"' for column in columns[1:])
				connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" ({", ".join(definitions)})')
		with closing(self._connect()) as connection:
			migrate(connection)

	def _get_catalog(self, table: str) -> list[dict]:
		if table not in CATALOG_COLUMNS:
			raise ValueError("Unknown catalog table")
		with closing(self._connect()) as connection, connection:
			rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
		if not rows:
			raise ValueError("Catalog is not seeded; run python -m backend.database.seed_data")
		records = [dict(row) for row in rows]
		if table == "customers":
			for record in records:
				if record["synthetic"] is not None:
					record["synthetic"] = bool(record["synthetic"])
		return records

	def get_packages(self) -> list[dict]:
		return self._get_catalog("packages")

	def get_addons(self) -> list[dict]:
		return self._get_catalog("addons")

	def get_customers(self) -> list[dict]:
		return self._get_catalog("customers")
