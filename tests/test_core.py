"""Unit tests for the deterministic core. Run: python -m pytest  (or python -m unittest)."""

import math
import unittest

from scanner import blackscholes as bs
from scanner import metrics as mx
from scanner import rules


class TestBlackScholes(unittest.TestCase):
    def test_put_price_positive_and_bounded(self):
        p = bs.bs_put_price(S=25000, K=23000, T=45 / 365, r=0.065, q=0.012, sigma=0.15)
        self.assertGreater(p, 0)
        self.assertLess(p, 23000)

    def test_iv_roundtrip(self):
        # price a put at a known vol, then recover that vol
        S, K, T, r, q, sigma = 25000, 23000, 45 / 365, 0.065, 0.012, 0.18
        price = bs.bs_put_price(S, K, T, r, q, sigma)
        iv = bs.implied_vol_put(price, S, K, T, r, q)
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, sigma, places=3)

    def test_iv_none_on_garbage(self):
        self.assertIsNone(bs.implied_vol_put(0, 25000, 23000, 0.1, 0.065, 0.012))
        self.assertIsNone(bs.implied_vol_put(999999, 25000, 23000, 0.1, 0.065, 0.012))

    def test_norm_cdf(self):
        self.assertAlmostEqual(bs.norm_cdf(0), 0.5, places=6)
        self.assertAlmostEqual(bs.norm_cdf(-1.96), 0.025, places=3)


class TestMetrics(unittest.TestCase):
    def test_realized_vol(self):
        closes = [100 * (1.001 ** i) for i in range(30)]  # steady drift, tiny vol
        rv = mx.realized_vol(closes, 20)
        self.assertIsNotNone(rv)
        self.assertGreaterEqual(rv, 0)

    def test_percentile_rank(self):
        series = list(range(1, 101))  # 1..100
        self.assertAlmostEqual(mx.percentile_rank(50, series), 50.0, places=0)
        self.assertEqual(mx.percentile_rank(100, series), 100.0)

    def test_skew_sign(self):
        # OTM put IV higher than ATM → positive skew in vol points
        self.assertAlmostEqual(mx.put_skew(0.15, 0.20), 5.0, places=6)


class TestRules(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "entry_gates": {"vix_percentile_max": 30, "skew_max": 6, "iv_rv_ratio_max": 1.3,
                            "min_open_interest": 500, "min_volume": 100, "max_cost_per_lot": 3000},
            "exit_rules": {"take_profit_mult": 3.0, "vix_spike_percentile": 70,
                           "time_stop_dte": 14, "force_close_dte": 1},
        }

    def test_all_gates_pass(self):
        m = {"vix_percentile": 20, "skew": 3, "iv_rv_ratio": 1.1,
             "open_interest": 1000, "volume": 500, "cost_per_lot": 2000}
        gates = rules.evaluate_entry_gates(m, self.cfg)
        self.assertTrue(rules.passes_all(gates))

    def test_missing_data_fails_closed(self):
        m = {"vix_percentile": None, "skew": 3, "iv_rv_ratio": 1.1,
             "open_interest": 1000, "volume": 500, "cost_per_lot": 2000}
        gates = rules.evaluate_entry_gates(m, self.cfg)
        self.assertFalse(rules.passes_all(gates))

    def test_exit_take_profit(self):
        pos = {"entry_premium": 10.0}
        d = rules.evaluate_exit(pos, current_premium=31.0, vix_percentile=40, dte=30, cfg=self.cfg)
        self.assertEqual(d["action"], "TAKE_PROFIT")

    def test_exit_force_close(self):
        pos = {"entry_premium": 10.0}
        d = rules.evaluate_exit(pos, current_premium=5.0, vix_percentile=10, dte=1, cfg=self.cfg)
        self.assertEqual(d["action"], "FORCE_CLOSE")

    def test_no_exit(self):
        pos = {"entry_premium": 10.0}
        d = rules.evaluate_exit(pos, current_premium=11.0, vix_percentile=40, dte=30, cfg=self.cfg)
        self.assertIsNone(d)


if __name__ == "__main__":
    unittest.main()
