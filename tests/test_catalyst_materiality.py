import unittest
from core.catalyst import assess_catalyst, catalyst_score

class CatalystMaterialityTests(unittest.TestCase):
    def event(self, text, **extra):
        row = {"text": text, "source_type": "NSE_CORPORATE_ANNOUNCEMENT",
               "announcement_date": "2026-10-10"}
        row.update(extra)
        return row

    def test_nonbinding_mou_never_gets_positive_credit(self):
        result = assess_catalyst([self.event("Non-binding MoU for exploratory green hydrogen partnership")], "2026-10-13")
        self.assertEqual(result["status"], "PROMOTIONAL")
        self.assertLess(result["score"], 55)

    def test_binding_material_named_customer_gets_credit(self):
        result = assess_catalyst([self.event("Binding contract awarded; order received",
            contract_value_cr=25, annual_revenue_cr=100, customer_name="Named Client")], "2026-10-13")
        self.assertEqual(result["status"], "MATERIAL")
        self.assertEqual(result["score"], 85)
        self.assertAlmostEqual(result["materiality_ratio"], .25)

    def test_below_twenty_percent_is_not_material(self):
        result = assess_catalyst([self.event("Firm purchase order",
            order_value_cr=10, revenue_cr=100, customer_name="Named Client")], "2026-10-13")
        self.assertEqual(result["status"], "IMMATERIAL")
        self.assertLess(result["score"], 55)

    def test_missing_timestamp_or_nonofficial_source_is_excluded(self):
        result = assess_catalyst([{"text": "binding contract order received",
            "source_type": "SCREENER_PUBLIC_FALLBACK"}], "2026-10-13")
        self.assertEqual(result["status"], "UNVERIFIED_OR_STALE_EVIDENCE")
        self.assertEqual(result["verified_events"], 0)
        self.assertLess(catalyst_score([{"text": "large order capacity expansion"}], "2026-10-13"), 55)

    def test_negative_event_overrides_positive_event(self):
        result = assess_catalyst([
            self.event("Binding contract awarded", contract_value_cr=50, annual_revenue_cr=100, customer_name="Client"),
            self.event("Order cancelled following delay")
        ], "2026-10-13")
        self.assertEqual(result["status"], "NEGATIVE")
        self.assertEqual(result["score"], 0)

    def test_routine_mention_of_no_delay_is_not_negative(self):
        result = assess_catalyst([self.event("Project remains on schedule with no delay")], "2026-10-13")
        self.assertEqual(result["status"], "UNVERIFIED")

    def test_future_event_is_not_point_in_time_eligible(self):
        result = assess_catalyst([self.event("Binding contract awarded", announcement_date="2026-10-20",
            contract_value_cr=50, annual_revenue_cr=100, customer_name="Client")], "2026-10-13")
        self.assertEqual(result["status"], "UNVERIFIED_OR_STALE_EVIDENCE")

if __name__ == "__main__":
    unittest.main()
