"""Read and update the customer's single current plan snapshot."""

from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from backend.database.db import SearchDatabase
from backend.services.tracing_service import record_event


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class CurrentPlanService:
    def __init__(self, database: SearchDatabase | None = None) -> None:
        self.database = database or SearchDatabase()

    def initialize_missing_plans(self) -> int:
        """Create snapshots for customers not yet initialized, without overwriting."""
        timestamp = datetime.now(timezone.utc).isoformat()
        with closing(self.database._connect()) as connection, connection:
            cursor = connection.execute("""
                INSERT OR IGNORE INTO current_plans (
                    customer_id, customer_name, package_id, package_name,
                    package_data_gb, package_voice_minutes, package_sms,
                    package_monthly_price_sar,
                    package_social_data_gb, package_roaming_data_gb, package_international_minutes,
                    package_multi_sim, package_data_sharing, package_contract_months, package_details_json,
                    package_included_benefits_json, addon_ids_json, addon_names_json,
                    addon_details_json, total_price, created_at, updated_at
                )
                SELECT c.customer_id, c.name, p.package_id, p.package_name,
                    p.data_gb, p.voice_minutes, p.sms,
                    p.monthly_price_sar,
                    p.social_data_gb, p.roaming_data_gb, p.international_minutes,
                    p.multi_sim, p.data_sharing, p.contract_months,
                    CASE WHEN p.package_id IS NULL THEN '{}' ELSE json_object(
                        'data_gb',p.data_gb,'social_data_gb',p.social_data_gb,'voice_minutes',p.voice_minutes,'sms',p.sms,
                        'monthly_price_sar',p.monthly_price_sar,
                        'streaming_benefit',p.streaming_benefit,'gaming_benefit',p.gaming_benefit,
                        'roaming_data_gb',p.roaming_data_gb,'international_minutes',p.international_minutes,
                        'multi_sim',p.multi_sim,'data_sharing',p.data_sharing,'contract_months',p.contract_months
                    ) END,
                    json_array(
                        CASE WHEN p.streaming_benefit IS NOT NULL AND p.streaming_benefit NOT IN ('', 'None') THEN p.streaming_benefit END,
                        CASE WHEN p.gaming_benefit IS NOT NULL AND p.gaming_benefit NOT IN ('', 'None') THEN p.gaming_benefit END
                    ), '[]', '[]', '[]', c.current_price_sar, ?, ?
                FROM customers c LEFT JOIN packages p ON p.package_id = c.current_package_id
            """, (timestamp, timestamp))
            initialized = cursor.rowcount
        record_event("current_plans.initialized", inputs={"source": "customers/packages/addons"},
                     outputs={"inserted_customer_rows": initialized},
                     metadata={"entity": "current_plans", "stage": "INITIALIZATION"},
                     tags=("database", "current-plan"))
        return initialized

    def get_current_plan(self, customer_id: str) -> dict | None:
        with closing(self.database._connect()) as connection:
            row = connection.execute("SELECT * FROM current_plans WHERE customer_id = ?", (customer_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for column in ("package_included_benefits_json", "package_details_json", "addon_ids_json", "addon_names_json", "addon_details_json"):
            result[column.removesuffix("_json")] = json.loads(result.pop(column))
        result["package_included_benefits"] = [value for value in result["package_included_benefits"] if value is not None]
        return result

    def replace_current_plan(self, customer_id: str, package_id: str, addon_ids: list[str]) -> dict:
        """Resolve all snapshot fields and price from catalogs; update in place."""
        with closing(self.database._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = datetime.now(timezone.utc).isoformat()
            self.replace_current_plan_in_transaction(connection, customer_id, package_id, addon_ids, timestamp)
        return self.get_current_plan(customer_id)

    @staticmethod
    def catalog_composition(connection, package_ids: list[str], addon_ids: list[str]):
        """Resolve and validate a package/add-on composition from SQLite catalogs."""
        if len(package_ids) != 1 or len(set(package_ids)) != 1:
            raise ValueError("A selected solution must contain exactly one unique package")
        if len(addon_ids) > 2 or len(set(addon_ids)) != len(addon_ids):
            raise ValueError("A selected solution may contain up to two unique add-ons")
        package = connection.execute("SELECT * FROM packages WHERE package_id=?", (package_ids[0],)).fetchone()
        if package is None:
            raise ValueError(f"Unknown package: {package_ids[0]}")
        addons = []
        for addon_id in addon_ids:
            addon = connection.execute("SELECT * FROM addons WHERE addon_id=?", (addon_id,)).fetchone()
            if addon is None:
                raise ValueError(f"Unknown add-on: {addon_id}")
            addons.append(addon)
        prices = [package["monthly_price_sar"], *(addon["monthly_price_sar"] for addon in addons)]
        if any(value is None for value in prices):
            raise ValueError("A catalog price is missing for the selected solution")
        total = sum((Decimal(str(value)) for value in prices), Decimal("0"))
        total_value = int(total) if total == total.to_integral_value() else float(total)
        return package, addons, total_value

    @staticmethod
    def selected_plan_snapshot(customer_id: str, customer_name: str, package, addons: list, total_price) -> dict:
        """Build a full target-plan snapshot using catalog rows, not submitted labels/prices."""
        return {
            "customer_id": customer_id, "customer_name": customer_name,
            "package_ids": [package["package_id"]], "package_names": [package["package_name"]],
            "package_data_gb": package["data_gb"], "package_voice_minutes": package["voice_minutes"],
            "package_sms": package["sms"], "package_monthly_price_sar": package["monthly_price_sar"],
            "package_social_data_gb": package["social_data_gb"],
            "package_roaming_data_gb": package["roaming_data_gb"],
            "package_international_minutes": package["international_minutes"],
            "package_multi_sim": package["multi_sim"], "package_data_sharing": package["data_sharing"],
            "package_contract_months": package["contract_months"],
            "package_included_benefits": [package[key] for key in ("streaming_benefit", "gaming_benefit")
                                          if package[key] not in (None, "", "None")],
            "package_details": {key: package[key] for key in (
                "data_gb", "social_data_gb", "voice_minutes", "sms", "monthly_price_sar",
                "streaming_benefit", "gaming_benefit", "roaming_data_gb", "international_minutes",
                "multi_sim", "data_sharing", "contract_months")},
            "addon_ids": [item["addon_id"] for item in addons],
            "addon_names": [item["addon_name"] for item in addons],
            "total_price": total_price, "addon_details": [dict(item) for item in addons],
        }

    @staticmethod
    def replace_current_plan_in_transaction(connection, customer_id: str, package_id: str,
                                            addon_ids: list[str], timestamp: str | None = None) -> None:
        timestamp = timestamp or datetime.now(timezone.utc).isoformat()
        customer = connection.execute("SELECT name FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()
        package = connection.execute("SELECT * FROM packages WHERE package_id = ?", (package_id,)).fetchone()
        if customer is None:
            raise ValueError(f"Unknown customer: {customer_id}")
        if package is None:
            raise ValueError(f"Unknown package: {package_id}")
        if len(addon_ids) != len(set(addon_ids)):
            raise ValueError("Duplicate add-on IDs are not allowed")
        addons = []
        for addon_id in addon_ids:
            addon = connection.execute("SELECT * FROM addons WHERE addon_id = ?", (addon_id,)).fetchone()
            if addon is None:
                raise ValueError(f"Unknown add-on: {addon_id}")
            addons.append(dict(addon))
        price_values = [package["monthly_price_sar"], *(a["monthly_price_sar"] for a in addons)]
        if any(value is None for value in price_values):
            raise ValueError("Catalog price is missing for the selected plan")
        catalog_total = sum((Decimal(str(value)) for value in price_values), Decimal("0"))
        total_price = int(catalog_total) if catalog_total == catalog_total.to_integral_value() else float(catalog_total)
        benefits = [package[key] for key in ("streaming_benefit", "gaming_benefit")
                    if package[key] not in (None, "", "None")]
        existing = connection.execute("SELECT created_at FROM current_plans WHERE customer_id = ?", (customer_id,)).fetchone()
        created_at = existing[0] if existing else timestamp
        connection.execute("""
            INSERT INTO current_plans (
                customer_id, customer_name, package_id, package_name,
                package_data_gb, package_voice_minutes, package_sms,
                package_monthly_price_sar,
                package_social_data_gb, package_roaming_data_gb, package_international_minutes,
                package_multi_sim, package_data_sharing, package_contract_months, package_details_json,
                package_included_benefits_json, addon_ids_json, addon_names_json,
                addon_details_json, total_price, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(customer_id) DO UPDATE SET
                customer_name=excluded.customer_name, package_id=excluded.package_id,
                package_name=excluded.package_name, package_data_gb=excluded.package_data_gb,
                package_voice_minutes=excluded.package_voice_minutes, package_sms=excluded.package_sms,
                package_monthly_price_sar=excluded.package_monthly_price_sar,
                package_social_data_gb=excluded.package_social_data_gb,
                package_roaming_data_gb=excluded.package_roaming_data_gb,
                package_international_minutes=excluded.package_international_minutes,
                package_multi_sim=excluded.package_multi_sim, package_data_sharing=excluded.package_data_sharing,
                package_contract_months=excluded.package_contract_months, package_details_json=excluded.package_details_json,
                package_included_benefits_json=excluded.package_included_benefits_json,
                addon_ids_json=excluded.addon_ids_json, addon_names_json=excluded.addon_names_json,
                addon_details_json=excluded.addon_details_json, total_price=excluded.total_price,
                updated_at=excluded.updated_at
        """, (customer_id, customer[0], package_id, package["package_name"], package["data_gb"],
              package["voice_minutes"], package["sms"], package["monthly_price_sar"],
              package["social_data_gb"], package["roaming_data_gb"],
              package["international_minutes"], package["multi_sim"], package["data_sharing"], package["contract_months"],
              _json({key: package[key] for key in ("data_gb", "social_data_gb", "voice_minutes", "sms", "monthly_price_sar",
                    "streaming_benefit", "gaming_benefit", "roaming_data_gb", "international_minutes",
                    "multi_sim", "data_sharing", "contract_months")}), _json(benefits), _json(addon_ids),
              _json([a["addon_name"] for a in addons]), _json(addons), total_price, created_at, timestamp))
