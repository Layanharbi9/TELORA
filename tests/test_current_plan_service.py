import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.database.db import SearchDatabase
from backend.database.seed_data import seed_data
from backend.services.current_plan_service import CurrentPlanService


class CurrentPlanServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "telora.sqlite3"
        self.database = SearchDatabase(self.path)
        seed_data(database_path=self.path)
        self.service = CurrentPlanService(self.database)

    def test_seed_initializes_one_catalog_grounded_snapshot_per_customer(self):
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM customers").fetchone()[0],
                             connection.execute("SELECT count(*) FROM current_plans").fetchone()[0])
        plan = self.service.get_current_plan("CUST-01")
        customer = next(row for row in self.database.get_customers() if row["customer_id"] == "CUST-01")
        package = next(row for row in self.database.get_packages()
                       if row["package_id"] == customer["current_package_id"])
        self.assertEqual(plan["package_id"], package["package_id"])
        self.assertEqual(plan["package_name"], package["package_name"])
        self.assertEqual(plan["package_data_gb"], package["data_gb"])
        self.assertEqual(plan["package_voice_minutes"], package["voice_minutes"])
        self.assertEqual(plan["package_sms"], package["sms"])
        self.assertEqual(plan["package_monthly_price_sar"], package["monthly_price_sar"])
        self.assertEqual(plan["package_included_benefits"], [package[key] for key in ("streaming_benefit", "gaming_benefit")
                                                               if package[key] not in (None, "", "None")])
        self.assertEqual(plan["total_price"], customer["current_price_sar"])
        self.assertEqual(plan["addon_ids"], [])
        self.assertEqual(plan["addon_names"], [])
        self.assertEqual(plan["addon_details"], [])
        for field, source_field in (("package_social_data_gb", "social_data_gb"),
                                    ("package_roaming_data_gb", "roaming_data_gb"),
                                    ("package_international_minutes", "international_minutes"),
                                    ("package_multi_sim", "multi_sim"),
                                    ("package_data_sharing", "data_sharing"),
                                    ("package_contract_months", "contract_months")):
            self.assertEqual(plan[field], package[source_field])
        self.assertEqual(self.service.initialize_missing_plans(), 0)

    def test_replace_refreshes_full_snapshot_without_adding_a_row(self):
        before = self.service.get_current_plan("CUST-01")
        package = next(p for p in self.database.get_packages() if p["package_id"] != before["package_id"])
        addon = self.database.get_addons()[0]
        updated = self.service.replace_current_plan("CUST-01", package["package_id"], [addon["addon_id"]])
        self.assertEqual(updated["package_name"], package["package_name"])
        self.assertEqual(updated["package_data_gb"], package["data_gb"])
        self.assertEqual(updated["package_voice_minutes"], package["voice_minutes"])
        self.assertEqual(updated["package_sms"], package["sms"])
        self.assertEqual(updated["addon_names"], [addon["addon_name"]])
        self.assertEqual(updated["total_price"], package["monthly_price_sar"] + addon["monthly_price_sar"])
        self.assertEqual(updated["created_at"], before["created_at"])
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM current_plans WHERE customer_id='CUST-01'").fetchone()[0], 1)

    def test_initialization_fills_missing_customers_without_overwriting_existing_plans(self):
        original = self.service.get_current_plan("CUST-01")
        package = next(p for p in self.database.get_packages() if p["package_id"] != original["package_id"])
        updated = self.service.replace_current_plan("CUST-01", package["package_id"], [])
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM current_plans WHERE customer_id='CUST-02'")

        inserted = self.service.initialize_missing_plans()

        self.assertEqual(inserted, 1)
        self.assertEqual(self.service.get_current_plan("CUST-01"), updated)
        with sqlite3.connect(self.path) as connection:
            customer_count = connection.execute("SELECT count(*) FROM customers").fetchone()[0]
            plan_count = connection.execute("SELECT count(*) FROM current_plans").fetchone()[0]
            duplicate_count = connection.execute("""SELECT count(*) FROM (
                SELECT customer_id FROM current_plans GROUP BY customer_id HAVING count(*) > 1
            )""").fetchone()[0]
        self.assertEqual(plan_count, customer_count)
        self.assertEqual(duplicate_count, 0)

    def test_unknown_catalog_values_leave_snapshot_unchanged(self):
        before = self.service.get_current_plan("CUST-01")
        with self.assertRaises(ValueError):
            self.service.replace_current_plan("CUST-01", "MISSING", [])
        self.assertEqual(self.service.get_current_plan("CUST-01"), before)


if __name__ == "__main__":
    unittest.main()
