import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from core.pit_store import load_facts
from core.nse_pit import snapshot
from scripts.run_winner_audit import build_historical_funnel


class PITGuardTests(unittest.TestCase):
    def test_load_facts_rejects_future_availability_and_future_period(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "facts.jsonl"
            rows = [
                {"symbol":"A","metric":"revenue","value":10,"period_end":"2025-12-31","available_at":"2026-02-01"},
                {"symbol":"B","metric":"revenue","value":20,"period_end":"2026-12-31","available_at":"2026-02-01"},
                {"symbol":"C","metric":"revenue","value":30,"period_end":"2025-12-31","available_at":"2026-05-01"},
            ]
            p.write_text("\n".join(json.dumps(x) for x in rows), encoding="utf-8")
            got = load_facts("2026-03-01", root=tmp)
            self.assertEqual(got.symbol.tolist(), ["A"])

    def test_snapshot_rejects_future_period_even_if_available(self):
        rows = [
            {"symbol":"A","metric":"pat","value":1,"period_end":"2025-12-31","available_at":"2026-02-01"},
            {"symbol":"B","metric":"pat","value":2,"period_end":"2026-12-31","available_at":"2026-02-01"},
        ]
        got = snapshot(rows, "2026-03-01")
        self.assertEqual([x["symbol"] for x in got], ["A"])

    def test_historical_funnel_uses_deterministic_arms_and_reports_union(self):
        px = pd.DataFrame([
            {"symbol":"A","close":100,"ret_21d":.10,"ret_63d":.15,"ret_126d":.20,"ret_252d":.35,"avg_turnover_60d":5e7,"pct_off_high":-.03},
            {"symbol":"B","close":80,"ret_21d":.04,"ret_63d":.01,"ret_126d":.10,"ret_252d":.12,"avg_turnover_60d":4e7,"pct_off_high":-.10},
            {"symbol":"C","close":50,"ret_21d":-.02,"ret_63d":-.10,"ret_126d":-.20,"ret_252d":-.30,"avg_turnover_60d":3e7,"pct_off_high":-.40},
            {"symbol":"D","close":30,"ret_21d":.02,"ret_63d":.01,"ret_126d":.03,"ret_252d":.04,"avg_turnover_60d":2e7,"pct_off_high":-.08},
        ])
        fake_facts = pd.DataFrame([{"symbol":"D","metric":"pat","value":1,"period_end":"2025-12-31","available_at":"2026-01-01"}])
        fake_scores = pd.DataFrame([{
            "symbol":"D","valuation_gap":70,"earnings_acceleration":75,
            "cash_conversion":65,"reinvestment_roic":72,"governance_balance_sheet":68,
            "fundamental_evidence":.85
        }])
        with patch("scripts.run_winner_audit.load_facts", return_value=fake_facts), \
             patch("scripts.run_winner_audit.score_fundamentals", return_value=fake_scores):
            screened, diag = build_historical_funnel(px, "2026-02-06")
        self.assertGreater(len(screened), 0)
        self.assertEqual(diag["union"], len(screened))
        self.assertIn("historical_pit_fundamental_arm", screened.columns)
        self.assertIn("no live Chartink/Screener membership", diag["method"])

    def test_validation_does_not_report_ratio_lift_on_negative_baseline(self):
        from multibagger.validation import _selection_lift_cluster_ci
        rows = []
        for date, returns in [
            ("2024-01-31", [-.20, -.15, -.10, -.05]),
            ("2024-02-29", [-.30, -.25, -.20, -.10]),
        ]:
            for score, ret in enumerate(returns, start=1):
                rows.append({"decision_date": date, "fundamental_score": score,
                             "forward_return": ret})
        result = _selection_lift_cluster_ci(pd.DataFrame(rows), n_boot=100, seed=7)
        self.assertEqual(result["ratio_valid_denominator_dates"], 0)
        self.assertIsNone(result["ratio_mean_where_valid"])
        self.assertLess(result["difference_in_means"], 0)


if __name__ == "__main__":
    unittest.main()
