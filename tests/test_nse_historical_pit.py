import unittest

from core.nse_historical_pit import _filing_rows, _parse_xbrl


class NSEHistoricalPITTests(unittest.TestCase):
    def test_filing_rows_keep_public_filing_timestamp_and_xbrl_url(self):
        rows = _filing_rows({
            "data": [{
                "symbol": "ABC",
                "filingDate": "12-Jun-2025 18:30:00",
                "xbrl": "/corporates/xbrl/abc.xml",
            }]
        }, "ABC")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "ABC")
        self.assertEqual(rows[0]["available_at"], "2025-06-12T18:30:00")
        self.assertEqual(rows[0]["xbrl_url"], "https://www.nseindia.com/corporates/xbrl/abc.xml")

    def test_minute_precision_filing_date_falls_back_to_broadcast_timestamp(self):
        rows = _filing_rows({
            "data": [{
                "symbol": "ABC",
                "filingDate": "16-Jan-2025 20:20",
                "broadcastDate": "16-Jan-2025 20:20:21",
                "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/abc.xml",
            }]
        }, "ABC")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["available_at"], "2025-01-16T20:20:00")
        self.assertEqual(rows[0]["xbrl_url"], "https://nsearchives.nseindia.com/corporate/xbrl/abc.xml")

    def test_xbrl_prefers_quarter_flow_and_primary_instant_context(self):
        xml = b"""
        <xbrl>
          <context id="OneD"><entity><identifier>ABC</identifier></entity>
            <period><startDate>2026-01-01</startDate><endDate>2026-03-31</endDate></period></context>
          <context id="FourD"><entity><identifier>ABC</identifier></entity>
            <period><startDate>2025-04-01</startDate><endDate>2026-03-31</endDate></period></context>
          <context id="OneI"><entity><identifier>ABC</identifier></entity>
            <period><instant>2026-03-31</instant></period></context>
          <context id="FourI"><entity><identifier>ABC</identifier></entity>
            <period><instant>2026-03-31</instant></period></context>
          <context id="SegmentD"><entity><identifier>ABC</identifier>
            <segment><explicitMember dimension="SegmentAxis">Retail</explicitMember></segment></entity>
            <period><startDate>2026-01-01</startDate><endDate>2026-03-31</endDate></period></context>
          <DateOfEndOfReportingPeriod>31-Mar-2026</DateOfEndOfReportingPeriod>
          <NatureOfReportStandaloneConsolidated>Consolidated</NatureOfReportStandaloneConsolidated>
          <RevenueFromOperations contextRef="OneD">100</RevenueFromOperations>
          <RevenueFromOperations contextRef="FourD">900</RevenueFromOperations>
          <RevenueFromOperations contextRef="SegmentD">25</RevenueFromOperations>
          <ProfitLossForPeriod contextRef="OneD">10</ProfitLossForPeriod>
          <ProfitLossForPeriod contextRef="FourD">60</ProfitLossForPeriod>
          <BorrowingsCurrentAndNonCurrent contextRef="OneI">200</BorrowingsCurrentAndNonCurrent>
          <BorrowingsCurrentAndNonCurrent contextRef="FourI">210</BorrowingsCurrentAndNonCurrent>
        </xbrl>
        """
        filing = {
            "symbol": "ABC",
            "available_at": "2026-05-15T12:00:00",
            "xbrl_url": "https://www.nseindia.com/xbrl/abc.xml",
        }
        rows = _parse_xbrl(xml, filing)
        by_metric = {row["metric"]: row for row in rows}
        self.assertEqual(by_metric["revenue"]["value"], 100.0)
        self.assertEqual(by_metric["pat"]["value"], 10.0)
        self.assertEqual(by_metric["debt"]["value"], 200.0)
        self.assertEqual(by_metric["revenue"]["basis"], "consolidated")
        self.assertEqual(by_metric["revenue"]["period_end"], "2026-03-31")
        self.assertEqual(by_metric["revenue"]["available_at"], "2026-05-15T12:00:00")


if __name__ == "__main__":
    unittest.main()
