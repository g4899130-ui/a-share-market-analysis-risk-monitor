"""小型模拟价格仅验证公式，不作为真实股票分析结果。"""

import math
import unittest

import pandas as pd

from analysis import correlation, cumulative_returns, daily_returns, drawdown, summarize


class FinancialFormulaTests(unittest.TestCase):
    def setUp(self):
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"])
        self.prices = pd.DataFrame({"000001": [100.0, 110.0, 99.0, 120.0],
                                    "000002": [50.0, 55.0, 49.5, 60.0]}, index=dates)

    def test_return_and_first_day(self):
        result = daily_returns(self.prices)
        self.assertTrue(pd.isna(result.iloc[0, 0]))
        self.assertAlmostEqual(result.iloc[1, 0], 0.10)
        self.assertAlmostEqual(result.iloc[2, 0], -0.10)

    def test_missing_date_is_not_filled(self):
        prices = self.prices.copy()
        prices.loc[prices.index[1], "000002"] = float("nan")
        result = daily_returns(prices)
        self.assertTrue(pd.isna(result.iloc[1, 1]))
        self.assertTrue(pd.isna(result.iloc[2, 1]))

    def test_cumulative_uses_common_baseline(self):
        result = cumulative_returns(self.prices)
        self.assertAlmostEqual(result.iloc[-1, 0], 0.2)
        self.assertAlmostEqual(result.iloc[-1, 1], 0.2)

    def test_drawdown_uses_time_order(self):
        result = drawdown(self.prices["000001"])
        self.assertAlmostEqual(result.min(), -0.1)
        self.assertAlmostEqual(result.iloc[-1], 0)

    def test_volatility_and_correlation(self):
        summary, returns, _, _, corr, overlap = summarize(self.prices, {"000001": "A", "000002": "B"})
        expected_std = pd.Series([0.1, -0.1, 120 / 99 - 1]).std(ddof=1)
        self.assertAlmostEqual(summary.loc[0, "daily_return_std"], expected_std)
        self.assertAlmostEqual(summary.loc[0, "annualized_volatility"], expected_std * math.sqrt(252))
        self.assertAlmostEqual(corr.loc["000001", "000002"], 1.0)
        self.assertEqual(overlap.loc["000001", "000002"], 3)

    def test_correlation_uses_matching_dates_only(self):
        returns = pd.DataFrame({"A": [0.1, 0.2, 0.3, None], "B": [0.1, None, 0.3, 0.9]})
        corr, overlap = correlation(returns)
        self.assertEqual(overlap.loc["A", "B"], 2)
        self.assertAlmostEqual(corr.loc["A", "B"], 1.0)


if __name__ == "__main__":
    unittest.main()
