"""Tests on synthetic orders. No real orders, plans or customers."""
import unittest

from ledger_excerpts import (clean_order_id, detect_sitewide_markdowns,
                             parse_date_any, regression_warnings, to_money)


def order(oid, plan, price, date, fmt="PDF", platform="Marketplace A"):
    return {"order_id": oid, "platform": platform, "order_type": "STANDARD",
            "plan_name": plan, "deliverable_format": fmt, "base_plan_price": price,
            "total_price": price, "date": date, "_flags": []}


class TestHelpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(to_money("$1,295.50"), 1295.5)
        self.assertIsNone(to_money("call for price"))
        self.assertIsNone(to_money(None))

    def test_dates(self):
        self.assertEqual(parse_date_any("3rd September 2020"), "2020-09-03")
        self.assertEqual(parse_date_any("09/03/20"), "2020-09-03")
        self.assertEqual(parse_date_any("not a date"), "")

    def test_order_ids(self):
        self.assertEqual(clean_order_id("#1001"), "1001")
        self.assertEqual(clean_order_id(1001.0), "1001")


class TestSitewideMarkdowns(unittest.TestCase):
    def test_two_plans_same_discount_same_week_is_confirmed(self):
        rows = [order("1", "Plan One", 1000.00, "2020-08-01"),
                order("2", "Plan One", 900.00, "2020-09-03"),
                order("3", "Plan Two", 1500.00, "2020-07-01"),
                order("4", "Plan Two", 1350.00, "2020-09-05")]
        detect_sitewide_markdowns(rows, "Marketplace A")
        self.assertIn("SITEWIDE_SALE_CONFIRMED_10PCT_OFF_MULTI_PLAN_CLUSTER", rows[1]["_flags"])
        self.assertIn("SITEWIDE_SALE_CONFIRMED_10PCT_OFF_MULTI_PLAN_CLUSTER", rows[3]["_flags"])
        self.assertEqual(rows[0]["_flags"], [])

    def test_a_lone_match_is_only_suspected(self):
        rows = [order("1", "Plan One", 1000.00, "2020-08-01"),
                order("2", "Plan One", 850.00, "2021-02-10")]
        detect_sitewide_markdowns(rows, "Marketplace A")
        self.assertEqual(rows[1]["_flags"], ["SITEWIDE_SALE_SUSPECTED_15PCT_OFF_VERIFY"])

    def test_an_ordinary_price_change_is_left_alone(self):
        rows = [order("1", "Plan One", 1000.00, "2020-08-01"),
                order("2", "Plan One", 937.25, "2020-09-03")]
        detect_sitewide_markdowns(rows, "Marketplace A")
        self.assertEqual(rows[1]["_flags"], [])

    def test_other_channels_are_ignored(self):
        rows = [order("1", "Plan One", 1000.00, "2020-08-01", platform="Own store"),
                order("2", "Plan One", 900.00, "2020-09-03", platform="Own store")]
        detect_sitewide_markdowns(rows, "Marketplace A")
        self.assertEqual(rows[1]["_flags"], [])


class TestRegression(unittest.TestCase):
    BASELINE = [{"Platform": "Marketplace A", "Order #": "#1001", "Plan": "Plan One", "Price": "1,000.00"},
                {"Platform": "Marketplace A", "Order #": "#1002", "Plan": "Plan Two", "Price": "1,261.95"}]

    def test_either_price_interpretation_is_accepted(self):
        # the baseline folded a $261.95 add-on into its price for #1002
        now = [order("1001", "Plan One", 1000.00, "2020-08-01"),
               dict(order("1002", "Plan Two", 1000.00, "2020-08-02"), total_price=1261.95)]
        self.assertEqual(regression_warnings(now, self.BASELINE), [])

    def test_a_dropped_base_price_is_caught(self):
        # the defect the real check caught: base price missing from a bundle
        now = [dict(order("1002", "Plan Two", 0.00, "2020-08-02"), total_price=261.95)]
        w = regression_warnings(now, self.BASELINE)
        self.assertEqual(len(w), 1)
        self.assertIn("PRICE MISMATCH", w[0])

    def test_a_renamed_plan_is_caught(self):
        now = [order("1001", "Plan Three", 1000.00, "2020-08-01")]
        self.assertIn("PLAN MISMATCH", regression_warnings(now, self.BASELINE)[0])


if __name__ == "__main__":
    unittest.main()
