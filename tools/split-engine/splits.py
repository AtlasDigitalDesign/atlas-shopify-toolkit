"""Split engine. The percentages come from agency_config.json, not from here.

A processing fee is taken off the top of every input group; the configured
weights then run against the remaining "adjusted net".

All money is handled as Decimal and allocated in whole cents so that the fee
plus the routed cuts always sum back to the gross figure they came from.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
ONE = Decimal("1")

GROUP_KEYS = ("plans_and_addons", "modifications", "digital_assets")
SHARE_KEYS = ("marketing", "client", "agency")

# Configured weights are floats; allow a hair of slack when checking they total 1.
WEIGHT_TOLERANCE = Decimal("0.0000001")


def to_money(value) -> Decimal:
    """Coerce anything numeric-ish into a cent-quantized Decimal."""
    if isinstance(value, Decimal):
        d = value
    else:
        d = Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def _rate(value, label: str) -> Decimal:
    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError(f"{label} is not a number: {value!r}") from None
    if rate < 0 or rate > 1:
        raise ValueError(f"{label} must be between 0 and 1, got {rate}")
    return rate


@dataclass(frozen=True)
class GroupRules:
    """Shares of the adjusted net for one revenue group. Always totals 1."""

    marketing: Decimal
    client: Decimal
    agency: Decimal
    # Human-readable routing rule with {client}/{agency} code placeholders,
    # built when the config is parsed so it reflects whatever was configured.
    summary: str = ""

    def weights(self) -> list[Decimal]:
        return [self.marketing, self.client, self.agency]

    def describe(self, client_code: str, agency_code: str) -> str:
        return self.summary.format(client=client_code, agency=agency_code)


@dataclass(frozen=True)
class SplitRules:
    """The full rule set for a deployment."""

    cc_fee_rate: Decimal
    plans: GroupRules
    mods: GroupRules
    digital: GroupRules
    # Optional second basis for plans revenue, offered as a toggle in the UI.
    # Configure "alternate_split" under plans_and_addons to enable it.
    plans_alternate: GroupRules | None = None
    alternate_label: str = ""
    # Apps Script routinely takes 4-10s and occasionally much longer.
    webhook_timeout_seconds: int = 45

    @property
    def has_alternate(self) -> bool:
        return self.plans_alternate is not None

    def plans_rules(self, alternate: bool) -> GroupRules:
        """The plans rules to apply, honouring the alternate basis."""
        if alternate and self.plans_alternate is not None:
            return self.plans_alternate
        return self.plans

    def plans_split_label(self, alternate: bool) -> str:
        """Which basis an order used, as recorded in the ledger."""
        if alternate and self.plans_alternate is not None:
            return self.alternate_label
        return self.standard_label

    @property
    def standard_label(self) -> str:
        """The default basis, e.g. '60/40'."""
        total = self.plans.client + self.plans.agency
        if total == 0:
            return "standard"
        return (f"{pct(self.plans.client / total)}/"
                f"{pct(self.plans.agency / total)}")

    def group(self, key: str) -> GroupRules:
        return {"plans_and_addons": self.plans,
                "modifications": self.mods,
                "digital_assets": self.digital}[key]


def pct(value: Decimal) -> str:
    """Render a 0-1 weight as a tidy percentage: 0.075 -> '7.5', 0.10 -> '10'."""
    text = f"{value * 100:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def build_group_rules(spec: dict, label: str,
                      cc_fee_rate: Decimal = Decimal("0")) -> GroupRules:
    """Read one group's weights, accepting either supported shape.

    flat:            {"marketing": 0.05, "client": 0.90, "agency": 0.05}
    remainder_split: {"marketing": 0.10,
                      "remainder_split": {"client": 0.70, "agency": 0.30}}
    """
    if not isinstance(spec, dict):
        raise ValueError(f"{label} must be an object")

    marketing = _rate(spec.get("marketing", 0), f"{label}.marketing")

    if "remainder_split" in spec:
        split = spec["remainder_split"]
        if not isinstance(split, dict):
            raise ValueError(f"{label}.remainder_split must be an object")
        client_share = _rate(split.get("client", 0), f"{label}.remainder_split.client")
        agency_share = _rate(split.get("agency", 0), f"{label}.remainder_split.agency")
        total = client_share + agency_share
        if abs(total - ONE) > WEIGHT_TOLERANCE:
            raise ValueError(
                f"{label}.remainder_split must total 1.0, got {total}")
        remainder = ONE - marketing
        summary = (f"{pct(cc_fee_rate)}% fee \u2192 {pct(marketing)}% mktg "
                   f"\u2192 {pct(client_share)}/{pct(agency_share)} net")
        return GroupRules(marketing=marketing,
                          client=remainder * client_share,
                          agency=remainder * agency_share,
                          summary=summary)

    client = _rate(spec.get("client", 0), f"{label}.client")
    agency = _rate(spec.get("agency", 0), f"{label}.agency")
    total = marketing + client + agency
    if abs(total - ONE) > WEIGHT_TOLERANCE:
        raise ValueError(
            f"{label} weights must total 1.0 (marketing + client + agency), "
            f"got {total}")
    if marketing == 0:
        summary = (f"{pct(cc_fee_rate)}% fee \u2192 "
                   f"{pct(client)}/{pct(agency)} {{client}}/{{agency}}")
    else:
        summary = (f"{pct(cc_fee_rate)}% fee \u2192 "
                   f"{pct(client)}/{pct(marketing)}/{pct(agency)} "
                   f"{{client}}/mktg/{{agency}}")
    return GroupRules(marketing=marketing, client=client, agency=agency,
                      summary=summary)


def build_rules(splits: dict) -> SplitRules:
    """Turn the `splits` block of agency_config.json into validated rules."""
    if not isinstance(splits, dict):
        raise ValueError("splits must be an object")

    fee = _rate(splits.get("cc_fee_rate", 0), "splits.cc_fee_rate")

    # An optional second basis for the plans remainder, e.g. an even 50/50.
    plans_spec = splits.get("plans_and_addons", {})
    alternate = None
    alternate_label = ""
    alt_spec = plans_spec.get("alternate_split") if isinstance(plans_spec, dict) else None
    if isinstance(alt_spec, dict):
        merged = {"marketing": plans_spec.get("marketing", 0),
                  "remainder_split": {"client": alt_spec.get("client", 0),
                                      "agency": alt_spec.get("agency", 0)}}
        alternate = build_group_rules(merged, "splits.plans_and_addons."
                                              "alternate_split", fee)
        alternate_label = str(alt_spec.get(
            "label", f"{pct(Decimal(str(alt_spec.get('client', 0))))}/"
                     f"{pct(Decimal(str(alt_spec.get('agency', 0))))}"))

    return SplitRules(
        plans_alternate=alternate,
        alternate_label=alternate_label,
        cc_fee_rate=fee,
        plans=build_group_rules(splits.get("plans_and_addons", {}),
                                "splits.plans_and_addons", fee),
        mods=build_group_rules(splits.get("modifications", {}),
                               "splits.modifications", fee),
        digital=build_group_rules(splits.get("digital_assets", {}),
                                  "splits.digital_assets", fee),
        webhook_timeout_seconds=int(splits.get("webhook_timeout_seconds", 45)),
    )


def allocate(gross: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `gross` across `weights` using the largest-remainder method.

    Every share lands within one cent of its exact value and the shares
    always add up to `gross` exactly - no drifting pennies.
    """
    gross = to_money(gross)
    if gross == 0:
        return [Decimal("0.00") for _ in weights]

    total_cents = int((gross / CENT).to_integral_value(rounding=ROUND_HALF_UP))
    sign = -1 if total_cents < 0 else 1
    total_cents = abs(total_cents)

    exact = [Decimal(total_cents) * w for w in weights]
    floors = [int(e // 1) for e in exact]
    remainder = total_cents - sum(floors)

    # Hand the leftover cents to the largest fractional parts first.
    order = sorted(
        range(len(weights)),
        key=lambda i: (exact[i] - floors[i], weights[i]),
        reverse=True,
    )
    for i in order[:remainder]:
        floors[i] += 1

    return [Decimal(sign * c) * CENT for c in floors]


@dataclass(frozen=True)
class GroupSplit:
    """The processing fee and routed cuts for a single revenue group."""

    gross: Decimal      # what the customer paid
    fee: Decimal        # taken by the card processor
    net: Decimal        # adjusted net the split rules ran against
    client: Decimal
    agency: Decimal
    marketing: Decimal


@dataclass(frozen=True)
class OrderSplit:
    """Full breakdown for an order across all three revenue groups."""

    plans: GroupSplit
    mods: GroupSplit
    digital: GroupSplit

    @property
    def _groups(self) -> tuple[GroupSplit, GroupSplit, GroupSplit]:
        return (self.plans, self.mods, self.digital)

    def _sum(self, attr: str) -> Decimal:
        return sum((getattr(g, attr) for g in self._groups), Decimal("0.00"))

    @property
    def gross_total(self) -> Decimal:
        return self._sum("gross")

    @property
    def fee_total(self) -> Decimal:
        return self._sum("fee")

    @property
    def net_total(self) -> Decimal:
        """Adjusted net across all groups - gross less the processing fee."""
        return self._sum("net")

    @property
    def client_total(self) -> Decimal:
        return self._sum("client")

    @property
    def agency_total(self) -> Decimal:
        return self._sum("agency")

    @property
    def marketing_total(self) -> Decimal:
        return self._sum("marketing")

    @property
    def net_plans(self) -> Decimal:
        """Plans revenue left after the fee and the marketing pool."""
        return self.plans.net - self.plans.marketing


def split_group(gross, rules: GroupRules, cc_fee_rate: Decimal) -> GroupSplit:
    """Take the fee off the top, then route the adjusted net by `rules`.

    The fee and every cut are allocated from the gross in a single pass, so the
    parts reconcile to the gross exactly and the cuts reconcile to the net.
    """
    gross = to_money(gross)
    net_rate = ONE - cc_fee_rate
    shares = allocate(gross, [cc_fee_rate] + [net_rate * w for w in rules.weights()])
    fee, marketing, client, agency = shares
    return GroupSplit(gross=gross, fee=fee, net=gross - fee,
                      client=client, agency=agency, marketing=marketing)


def calculate(plans_gross, mods_gross, digital_gross, rules: SplitRules,
              plans_alternate: bool = False) -> OrderSplit:
    """Route a full order through all three group rule sets."""
    return OrderSplit(
        plans=split_group(plans_gross, rules.plans_rules(plans_alternate),
                          rules.cc_fee_rate),
        mods=split_group(mods_gross, rules.mods, rules.cc_fee_rate),
        digital=split_group(digital_gross, rules.digital, rules.cc_fee_rate),
    )


def fmt(value: Decimal) -> str:
    """Render a Decimal as $0.00 with thousands separators."""
    return f"${to_money(value):,.2f}"
