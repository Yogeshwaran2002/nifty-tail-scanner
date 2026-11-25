"""Unit tests for the deterministic core. Run: python -m pytest  (or python -m unittest)."""

import math
import unittest

from scanner import blackscholes as bs
from scanner import metrics as mx
from scanner import rules
from scanner import strategies as strat


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

    def test_put_delta_atm(self):
        # ATM put delta is near -0.5
        d = bs.put_delta(25000, 25000, 45 / 365, 0.065, 0.012, 0.15)
        self.assertIsNotNone(d)
        self.assertTrue(-0.6 < d < -0.4)
        # deep OTM put delta near 0
        d2 = bs.put_delta(25000, 20000, 45 / 365, 0.065, 0.012, 0.15)
        self.assertTrue(-0.1 < d2 <= 0)


class TestStrategies(unittest.TestCase):
    def _cfg(self):
        return {
            "market": {"lot_size": 65, "risk_free_rate": 0.065, "dividend_yield": 0.012,
                       "trading_days_year": 252, "underlying": "NIFTY"},
            "entry_gates": {"min_open_interest": 500},
            "credit_spread": {"target_short_delta": 0.22, "width_points": 100,
                              "min_credit_to_width": 0.18, "min_vrp": 2.0,
                              "min_iv_rv_ratio": 1.10, "dte_min": 20, "dte_max": 45},
            "etf_momentum": {"top_n": 2, "lookback_3m_days": 2, "lookback_6m_days": 4, "basket": []},
        }

    def test_etf_return(self):
        # 10% rise over `lookback` steps
        closes = [100, 101, 110]
        self.assertAlmostEqual(strat._ret(closes, 2), 10.0, places=2)
        self.assertIsNone(strat._ret([100], 2))

    def test_credit_spread_math(self):
        cfg = self._cfg()
        spot, dte = 25000.0, 30
        T = dte / 365.0
        # synthetic OTM puts priced at a known vol so deltas/credit are sane
        puts = []
        for k in range(23000, 25000, 50):
            price = bs.bs_put_price(spot, k, T, 0.065, 0.012, 0.16)
            puts.append({"strike": float(k), "close": price, "open_interest": 1000, "volume": 500})
        out = strat.credit_spread(puts, spot, "2026-12-29", dte, 0.16, 0.14, 20.0,
                                  0.065, 0.012, cfg)
        self.assertEqual(out["long_strike"], out["short_strike"] - 100)
        # max loss = (width - credit) * lot, and defined/positive
        expected = (out["width"] - out["credit"]) * 65
        self.assertAlmostEqual(out["max_loss"], round(expected, 2), places=1)
        self.assertGreater(out["max_loss"], 0)
        self.assertTrue(50 < out["prob_profit_pct"] < 95)  # OTM short put
        self.assertIsInstance(out["gates"], list)


class TestMetrics(unittest.TestCase):
    def test_realized_vol(self):
        closes = [100 * (1.001 ** i) for i in range(30)]  # steady drift, tiny vol
        rv = mx.realized_vol(closes, 20)
        self.assertIsNotNone(rv)
        self.assertGreaterEqual(rv, 0)

    def test_yang_zhang_vol(self):
        # flat OHLC → zero variance → None (degenerate)
        flat = [{"open": 100, "high": 100, "low": 100, "close": 100} for _ in range(25)]
        self.assertIsNone(mx.yang_zhang_vol(flat, 20))
        # bars with a real range → positive annualized vol
        bars = [{"open": (b := 100 + i * 0.1), "high": b * 1.01, "low": b * 0.99, "close": b * 1.002}
                for i in range(25)]
        yz = mx.yang_zhang_vol(bars, 20)
        self.assertIsNotNone(yz)
        self.assertGreater(yz, 0)
        self.assertIsNone(mx.yang_zhang_vol(bars, 100))  # not enough bars

    def test_percentile_rank(self):
        series = list(range(1, 101))  # 1..100
        self.assertAlmostEqual(mx.percentile_rank(50, series), 50.0, places=0)
        self.assertEqual(mx.percentile_rank(100, series), 100.0)

    def test_skew_sign(self):
        # OTM put IV higher than ATM → positive skew in vol points
        self.assertAlmostEqual(mx.put_skew(0.15, 0.20), 5.0, places=6)

    def test_vol_risk_premium(self):
        # IV 18%, RV 12% → +6 vol points premium
        self.assertAlmostEqual(mx.vol_risk_premium(0.18, 0.12), 6.0, places=6)
        self.assertIsNone(mx.vol_risk_premium(None, 0.12))

    def test_trade_stats_tail_shape(self):
        # 9 small losses, 1 big win — classic tail-risk payoff
        pnls = [-100] * 9 + [5000]
        s = mx.trade_stats(pnls)
        self.assertEqual(s["total"], 10)
        self.assertEqual(s["wins"], 1)
        self.assertEqual(s["hit_rate_pct"], 10.0)
        self.assertEqual(s["expectancy"], 410.0)        # (5000 - 900) / 10
        self.assertEqual(s["payoff_ratio"], 50.0)        # 5000 / 100
        self.assertEqual(s["best"], 5000)
        self.assertEqual(s["worst"], -100)

    def test_trade_stats_empty(self):
        s = mx.trade_stats([])
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["payoff_ratio"])


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
