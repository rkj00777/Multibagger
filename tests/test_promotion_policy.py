import json
import tempfile
import unittest
from pathlib import Path

from multibagger.promotion_policy import production_validation_status


class ProductionPromotionPolicyTests(unittest.TestCase):
    def test_missing_report_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = production_validation_status(str(Path(tmp) / "missing.json"))
        self.assertFalse(result["approved"])
        self.assertEqual(result["reason"], "VALIDATION_REPORT_MISSING")

    def test_insufficient_dates_or_nonpositive_lift_blocks_production(self):
        report = {
            "status": "INSUFFICIENT_INDEPENDENT_SAMPLE",
            "horizon_validation": {
                "6m_126_sessions": {
                    "status": "INSUFFICIENT_INDEPENDENT_SAMPLE",
                    "statistical_validation": {"independent_decision_dates": 3},
                    "selection_lift_ci_low": -0.05,
                },
                "12m_252_sessions": {
                    "status": "INSUFFICIENT_INDEPENDENT_SAMPLE",
                    "statistical_validation": {"independent_decision_dates": 2},
                    "selection_lift_ci_low": -0.01,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            result = production_validation_status(str(path))
        self.assertFalse(result["approved"])
        self.assertIn("NEED_8_INDEPENDENT_DATES", result["reason"])
        self.assertIn("LIFT_CONFIDENCE_INTERVAL_NOT_POSITIVE", result["reason"])

    def test_validated_report_requires_positive_lift_ci_and_eight_dates(self):
        report = {
            "status": "VALIDATED_FOR_PRODUCTION",
            "horizon_validation": {
                "6m_126_sessions": {
                    "status": "VALIDATED",
                    "statistical_validation": {"independent_decision_dates": 12},
                    "selection_lift_ci_low": 0.02,
                },
                "12m_252_sessions": {
                    "status": "VALIDATED",
                    "statistical_validation": {"independent_decision_dates": 10},
                    "selection_lift_ci_low": 0.01,
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            result = production_validation_status(str(path))
        self.assertTrue(result["approved"])
        self.assertEqual(result["reason"], "VALIDATION_GATE_PASSED")


if __name__ == "__main__":
    unittest.main()
