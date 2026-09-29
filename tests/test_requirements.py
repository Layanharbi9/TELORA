import json
import unittest

from backend.schemas.requirements import NeedLevel, NeedSelection, RequirementSubmission, Requirements


def selection(need: str, level: str = "High") -> NeedSelection:
    return NeedSelection(need, level)


class RequirementsValidationTests(unittest.TestCase):
    def test_primary_need_only(self):
        requirements = Requirements(selection("Gaming"), None, None, 180)
        self.assertIsNone(requirements.secondary_need)

    def test_primary_and_secondary(self):
        requirements = Requirements(selection("Gaming"), selection("Streaming", "Medium"), None, 180)
        self.assertEqual(requirements.secondary_need.level, NeedLevel.MEDIUM)

    def test_primary_secondary_and_third(self):
        requirements = Requirements(selection("Gaming"), selection("Streaming"), selection("Calls", "Low"), 180)
        self.assertEqual(requirements.third_need.need, "Calls")

    def test_duplicate_needs(self):
        with self.assertRaises(ValueError):
            Requirements(selection("Gaming"), selection("Gaming"), None, 180)

    def test_missing_primary_need(self):
        with self.assertRaises(ValueError):
            Requirements(None, None, None, 180)

    def test_missing_usage_level(self):
        with self.assertRaises(ValueError):
            NeedSelection("Gaming", "")

    def test_invalid_need(self):
        with self.assertRaises(ValueError):
            NeedSelection("Travel", "High")

    def test_invalid_usage_level(self):
        with self.assertRaises(ValueError):
            NeedSelection("Gaming", "Extreme")

    def test_invalid_budget(self):
        for budget in (0, -1, "", "not-a-number"):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                Requirements(selection("Gaming"), None, None, budget)

    def test_valid_budget(self):
        requirements = Requirements(selection("Gaming"), None, None, "180")
        self.assertEqual(requirements.preferred_budget_sar, 180)

    def test_submission_contains_customer_identity(self):
        submission = RequirementSubmission("Layan", "CUST-001", Requirements(selection("Data"), None, None, 400))
        self.assertEqual(submission.to_dict()["username"], "Layan")
        self.assertEqual(submission.to_dict()["customer_id"], "CUST-001")

    def test_submission_rejects_missing_identity(self):
        requirements = Requirements(selection("Data"), None, None, 400)
        with self.assertRaises(ValueError):
            RequirementSubmission("", "CUST-001", requirements)
        with self.assertRaises(ValueError):
            RequirementSubmission("Layan", "", requirements)

    def test_submission_serializes_as_json(self):
        submission = RequirementSubmission("Layan", "CUST-001", Requirements(selection("Data"), None, None, 400))
        saved = json.loads(submission.to_json())
        self.assertEqual(saved["primary_need"]["need"], "Data")
        self.assertEqual(saved["preferred_budget_sar"], 400)


if __name__ == "__main__":
    unittest.main()