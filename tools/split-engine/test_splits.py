"""Split engine checks. Run: python -m unittest test_splits -v

These exercise the engine against configured rules, including a second,
made-up partner contract - the point of the template is that the maths
follows agency_config.json rather than anything hard-coded.
"""

import json
import unittest
from decimal import Decimal

from splits import build_rules, calculate

D = Decimal

# The example contract shipped in example_config.json (invented partner, invented terms).
EXAMPLE = json.load(open("example_config.json", encoding="utf-8"))["splits"]

# A different partner: 5% fee, 60/40 plans, 80/10/10 mods, 50/50 digital.
PARTNER = {
    "cc_fee_rate": 0.05,
    "plans_and_addons": {"marketing": 0.20,
                         "remainder_split": {"client": 0.60, "agency": 0.40}},
    "modifications": {"marketing": 0.10, "client": 0.80, "agency": 0.10},
    "digital_assets": {"marketing": 0.00, "client": 0.50, "agency": 0.50},
}


class TestConfigParsing(unittest.TestCase):
    def test_remainder_split_expands_to_shares_of_net(self):
        rules = build_rules(EXAMPLE)
        # 10% marketing, then 60/40 of the remaining 90%.
        self.assertEqual(rules.plans.marketing, D("0.10"))
        self.assertEqual(rules.plans.client, D("0.54"))
        self.assertEqual(rules.plans.agency, D("0.36"))

    def test_flat_shares_pass_through(self):
        rules = build_rules(EXAMPLE)
        self.assertEqual(rules.mods.client, D("0.85"))
        self.assertEqual(rules.mods.marketing, D("0.10"))
        self.assertEqual(rules.mods.agency, D("0.05"))

    def test_every_group_totals_one(self):
        for splits in (EXAMPLE, PARTNER):
            rules = build_rules(splits)
            for name in ("plans", "mods", "digital"):
                group = getattr(rules, name)
                with self.subTest(name=name):
                    self.assertEqual(sum(group.weights()), D("1"))

    def test_bad_weights_are_rejected(self):
        broken = json.loads(json.dumps(EXAMPLE))
        broken["modifications"]["client"] = 0.50      # no longer totals 1
        with self.assertRaises(ValueError):
            build_rules(broken)

    def test_out_of_range_fee_is_rejected(self):
        broken = json.loads(json.dumps(EXAMPLE))
        broken["cc_fee_rate"] = 1.5
        with self.assertRaises(ValueError):
            build_rules(broken)

    def test_rule_summaries_use_the_configured_codes(self):
        rules = build_rules(EXAMPLE)
        self.assertEqual(rules.plans.describe("EXS", "ADD"),
                         "3% fee → 10% mktg → 60/40 net")
        self.assertEqual(rules.mods.describe("EXS", "ADD"),
                         "3% fee → 85/10/5 EXS/mktg/ADD")


class TestExampleContract(unittest.TestCase):
    """The numbers this deployment must keep producing."""

    def setUp(self):
        self.rules = build_rules(EXAMPLE)

    def test_fee_is_taken_off_the_top(self):
        r = calculate(1000, 1000, 1000, self.rules)
        self.assertEqual(r.fee_total, D("90.00"))
        self.assertEqual(r.net_total, D("2910.00"))

    def test_plans_group(self):
        r = calculate(1000, 0, 0, self.rules)        # net = 970.00
        self.assertEqual(r.plans.marketing, D("97.00"))
        self.assertEqual(r.plans.client, D("523.80"))
        self.assertEqual(r.plans.agency, D("349.20"))

    def test_mods_group(self):
        r = calculate(0, 1000, 0, self.rules)
        self.assertEqual(r.mods.client, D("824.50"))
        self.assertEqual(r.mods.marketing, D("97.00"))
        self.assertEqual(r.mods.agency, D("48.50"))

    def test_digital_group(self):
        r = calculate(0, 0, 1000, self.rules)
        self.assertEqual(r.digital.agency, D("485.00"))
        self.assertEqual(r.digital.client, D("485.00"))
        self.assertEqual(r.digital.marketing, D("0.00"))


class TestPartnerContract(unittest.TestCase):
    """Change the config and the maths changes with it."""

    def setUp(self):
        self.rules = build_rules(PARTNER)

    def test_fee_follows_config(self):
        r = calculate(1000, 0, 0, self.rules)
        self.assertEqual(r.fee_total, D("50.00"))    # 5%, not 3%
        self.assertEqual(r.plans.net, D("950.00"))

    def test_plans_follow_config(self):
        r = calculate(1000, 0, 0, self.rules)        # net 950, 20% marketing
        self.assertEqual(r.plans.marketing, D("190.00"))
        self.assertEqual(r.plans.client, D("456.00"))   # 760 * 0.60
        self.assertEqual(r.plans.agency, D("304.00"))   # 760 * 0.40

    def test_digital_follows_config(self):
        r = calculate(0, 0, 1000, self.rules)
        self.assertEqual(r.digital.client, D("475.00"))
        self.assertEqual(r.digital.agency, D("475.00"))


class TestReconciliation(unittest.TestCase):
    CASES = [
        (0, 0, 0), (1000, 0, 0), (0, 1000, 0), (0, 0, 1000),
        (100.01, 33.33, 0.01), (1295.5, 450, 275), (0.03, 0.03, 0.03),
        (1234567.89, 987.65, 4321.09), (0.01, 0, 0), (999.99, 999.99, 999.99),
    ]

    def test_fee_and_cuts_sum_to_gross(self):
        for splits in (EXAMPLE, PARTNER):
            rules = build_rules(splits)
            for plans, mods, digital in self.CASES:
                with self.subTest(splits=splits["cc_fee_rate"], plans=plans):
                    r = calculate(plans, mods, digital, rules)
                    self.assertEqual(
                        r.fee_total + r.client_total + r.agency_total
                        + r.marketing_total, r.gross_total)

    def test_cuts_sum_to_adjusted_net(self):
        for splits in (EXAMPLE, PARTNER):
            rules = build_rules(splits)
            for plans, mods, digital in self.CASES:
                with self.subTest(plans=plans):
                    r = calculate(plans, mods, digital, rules)
                    self.assertEqual(
                        r.client_total + r.agency_total + r.marketing_total,
                        r.net_total)

    def test_each_group_reconciles(self):
        rules = build_rules(EXAMPLE)
        for plans, mods, digital in self.CASES:
            r = calculate(plans, mods, digital, rules)
            for group in (r.plans, r.mods, r.digital):
                with self.subTest(group=group):
                    self.assertEqual(
                        group.client + group.agency + group.marketing, group.net)
                    self.assertEqual(group.net + group.fee, group.gross)


if __name__ == "__main__":
    unittest.main()
