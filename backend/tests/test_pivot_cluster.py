"""Tests for pivot_cluster — VALIDATED_PIVOT_ZONE clustering.

Spec reference: MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.1.

Acceptance criteria from B6 task definition:
- 3+ components within tolerance → VALIDATED_PIVOT_ZONE created
- 2 components → not grouped (below min_contacts=3)
- Components too far apart → not grouped
- Tolerance is configurable parameter
- CompositeZone respects all invariants (envelope, exactly-one-primary)
- Test: cluster valid, non-cluster for distance, non-cluster for count < 3
"""

from __future__ import annotations

import math

import pytest

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent
from trading_lab.pivot_cluster import (
    PivotClusterResult,
    cluster_pivots,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_comp(
    price: float,
    source: str = "PIVOT_WICK",
    width: float = 0.0,
) -> ZoneComponent:
    """Create a ZoneComponent for testing.

    When width=0 the component is a line (lower==upper==price).
    When width>0 the component spans [price - width/2, price + width/2].
    """
    half = width / 2.0
    return ZoneComponent(
        source=source,
        price=price,
        lower_bound=price - half,
        upper_bound=price + half,
        is_primary=False,  # clustering ignores input is_primary
    )


# ── Valid cluster: 3 components within tolerance ──────────────────────────────


class TestValidCluster:
    """Three or more components within tolerance form a zone."""

    def test_three_close_pivots_create_zone(self):
        comps = [
            _make_comp(100.10),
            _make_comp(100.15),
            _make_comp(100.20),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 1
        assert len(result.unclustered) == 0
        zone = result.zones[0]
        assert zone.zone_type == ZoneType.VALIDATED_PIVOT_ZONE
        assert zone.status == ZoneStatus.ACTIVE
        assert len(zone.components) == 3

    def test_four_pivots_create_single_zone(self):
        comps = [
            _make_comp(50.00),
            _make_comp(50.02),
            _make_comp(50.04),
            _make_comp(50.06),
        ]
        result = cluster_pivots(comps, tolerance=0.05)

        assert len(result.zones) == 1
        assert len(zone := result.zones[0].components) == 4

    def test_envelope_matches_component_bounds(self):
        comps = [
            _make_comp(100.10, width=0.04),
            _make_comp(100.15, width=0.06),
            _make_comp(100.20, width=0.02),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        zone = result.zones[0]
        # lower_bound = min(100.10-0.02, 100.15-0.03, 100.20-0.01) = 100.08
        assert zone.lower_bound == pytest.approx(100.08)
        # upper_bound = max(100.10+0.02, 100.15+0.03, 100.20+0.01) = 100.21
        assert zone.upper_bound == pytest.approx(100.21)

    def test_exactly_one_primary_in_zone(self):
        comps = [
            _make_comp(200.00),
            _make_comp(200.05),
            _make_comp(200.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        zone = result.zones[0]
        primaries = [c for c in zone.components if c.is_primary]
        assert len(primaries) == 1

    def test_primary_is_closest_to_median(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        primary = result.zones[0].primary_component
        # Median is 100.05 — that component should be primary
        assert primary.price == pytest.approx(100.05)

    def test_primary_level_price_property(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert result.zones[0].primary_level_price == pytest.approx(100.05)

    def test_source_labels_preserved(self):
        comps = [
            _make_comp(100.00, source="PIVOT_WICK"),
            _make_comp(100.05, source="OB"),
            _make_comp(100.10, source="PIVOT_WICK"),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        sources = {c.source for c in result.zones[0].components}
        assert sources == {"PIVOT_WICK", "OB"}


# ── Non-cluster: too few contacts ─────────────────────────────────────────────


class TestTooFewContacts:
    """Clusters with fewer than min_contacts are not grouped."""

    def test_two_pivots_not_grouped(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 2

    def test_single_pivot_not_grouped(self):
        comps = [_make_comp(100.00)]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 1

    def test_empty_input(self):
        result = cluster_pivots([], tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 0

    def test_custom_min_contacts(self):
        """min_contacts=2 should group two close pivots."""
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
        ]
        result = cluster_pivots(comps, tolerance=0.10, min_contacts=2)

        assert len(result.zones) == 1
        assert len(result.unclustered) == 0


# ── Non-cluster: too far apart ────────────────────────────────────────────────


class TestTooFarApart:
    """Components beyond tolerance are not grouped together."""

    def test_three_pivots_all_distant(self):
        comps = [
            _make_comp(100.00),
            _make_comp(101.00),
            _make_comp(102.00),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 3

    def test_two_close_one_far(self):
        """Two close + one far = no zone (need 3 in cluster)."""
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(105.00),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 3

    def test_boundary_tolerance_included(self):
        """Components within tolerance distance are included."""
        comps = [
            _make_comp(100.00),
            _make_comp(100.08),
            _make_comp(100.16),
        ]
        # Each consecutive pair differs by 0.08, tolerance is 0.10
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 1

    def test_boundary_tolerance_excluded(self):
        """Components slightly beyond tolerance are excluded."""
        comps = [
            _make_comp(100.00),
            _make_comp(100.11),
            _make_comp(100.22),
        ]
        # Each pair differs by 0.11 > tolerance 0.10
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 0
        assert len(result.unclustered) == 3


# ── Multiple clusters ─────────────────────────────────────────────────────────


class TestMultipleClusters:
    """Input with multiple distinct clusters."""

    def test_two_separate_clusters(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
            _make_comp(200.00),
            _make_comp(200.05),
            _make_comp(200.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 2
        assert len(result.unclustered) == 0

        prices_0 = sorted(c.price for c in result.zones[0].components)
        prices_1 = sorted(c.price for c in result.zones[1].components)
        # One cluster around 100, one around 200
        assert prices_0[0] == pytest.approx(100.00)
        assert prices_1[0] == pytest.approx(200.00)

    def test_one_cluster_plus_unclustered(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
            _make_comp(300.00),  # isolated
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 1
        assert len(result.unclustered) == 1
        assert result.unclustered[0].price == pytest.approx(300.00)


# ── Input order independence ──────────────────────────────────────────────────


class TestInputOrder:
    """Clustering is order-independent (result should be same)."""

    def test_unsorted_input(self):
        comps = [
            _make_comp(100.16),
            _make_comp(100.00),
            _make_comp(100.08),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        assert len(result.zones) == 1
        assert len(result.zones[0].components) == 3

    def test_reverse_order_same_result(self):
        forward = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        reverse = list(reversed(forward))

        r_fwd = cluster_pivots(forward, tolerance=0.10)
        r_rev = cluster_pivots(reverse, tolerance=0.10)

        assert len(r_fwd.zones) == len(r_rev.zones) == 1
        fwd_prices = sorted(c.price for c in r_fwd.zones[0].components)
        rev_prices = sorted(c.price for c in r_rev.zones[0].components)
        assert fwd_prices == rev_prices


# ── Result metadata ───────────────────────────────────────────────────────────


class TestResultMetadata:
    """PivotClusterResult carries tolerance and min_contacts."""

    def test_tolerance_recorded(self):
        result = cluster_pivots([], tolerance=0.25)
        assert result.tolerance == pytest.approx(0.25)

    def test_min_contacts_recorded(self):
        result = cluster_pivots([], tolerance=0.10, min_contacts=5)
        assert result.min_contacts == 5


# ── Validation ────────────────────────────────────────────────────────────────


class TestValidation:
    """Parameter validation edge cases."""

    def test_tolerance_zero_raises(self):
        with pytest.raises(ValueError, match="tolerance must be > 0"):
            cluster_pivots([], tolerance=0.0)

    def test_tolerance_negative_raises(self):
        with pytest.raises(ValueError, match="tolerance must be > 0"):
            cluster_pivots([], tolerance=-0.05)

    def test_tolerance_nan_raises(self):
        with pytest.raises(ValueError, match="tolerance must be finite"):
            cluster_pivots([], tolerance=float("nan"))

    def test_tolerance_inf_raises(self):
        with pytest.raises(ValueError, match="tolerance must be finite"):
            cluster_pivots([], tolerance=float("inf"))

    def test_tolerance_bool_raises(self):
        with pytest.raises(TypeError, match="tolerance must be a number"):
            cluster_pivots([], tolerance=True)

    def test_min_contacts_zero_raises(self):
        with pytest.raises(ValueError, match="min_contacts must be >= 1"):
            cluster_pivots([], tolerance=0.10, min_contacts=0)

    def test_min_contacts_bool_raises(self):
        with pytest.raises(TypeError, match="min_contacts must be an int"):
            cluster_pivots([], tolerance=0.10, min_contacts=True)

    def test_components_not_list_raises(self):
        with pytest.raises(TypeError, match="components must be a list"):
            cluster_pivots(tuple(), tolerance=0.10)

    def test_non_zone_component_raises(self):
        with pytest.raises(TypeError, match="must be a ZoneComponent"):
            cluster_pivots([{"price": 100.0}], tolerance=0.10)


# ── Spec §7.1 specific: zone must not be artificially widened ─────────────────


class TestNoArtificialWidening:
    """The zone width is exactly the envelope of its components,
    not inflated by tolerance.
    """

    def test_zone_width_equals_component_span(self):
        comps = [
            _make_comp(100.00),  # line: lower=upper=100.00
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        zone = result.zones[0]
        # For line components, lower_bound=price and upper_bound=price
        assert zone.lower_bound == pytest.approx(100.00)
        assert zone.upper_bound == pytest.approx(100.10)
        # Width is exactly the spread, not tolerance-inflated
        assert (zone.upper_bound - zone.lower_bound) == pytest.approx(0.10)

    def test_zone_bounds_from_component_bounds_not_prices(self):
        """When components have width, zone uses their bounds."""
        comps = [
            _make_comp(100.00, width=0.10),  # 99.95 – 100.05
            _make_comp(100.05, width=0.10),  # 100.00 – 100.10
            _make_comp(100.10, width=0.10),  # 100.05 – 100.15
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        zone = result.zones[0]
        assert zone.lower_bound == pytest.approx(99.95)
        assert zone.upper_bound == pytest.approx(100.15)


# ── CompositeZone contract compliance ─────────────────────────────────────────


class TestCompositeZoneCompliance:
    """Every zone produced satisfies CompositeZone invariants."""

    def test_zone_is_frozen(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        zone = result.zones[0]
        with pytest.raises(AttributeError):
            zone.status = ZoneStatus.STORED

    def test_components_are_frozen(self):
        comps = [
            _make_comp(100.00),
            _make_comp(100.05),
            _make_comp(100.10),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        comp = result.zones[0].components[0]
        with pytest.raises(AttributeError):
            comp.price = 999.99

    def test_to_dict_roundtrip(self):
        comps = [
            _make_comp(100.00, source="PIVOT_WICK"),
            _make_comp(100.05, source="OB"),
            _make_comp(100.10, source="PIVOT_WICK"),
        ]
        result = cluster_pivots(comps, tolerance=0.10)

        d = result.zones[0].to_dict()
        assert d["zone_type"] == "VALIDATED_PIVOT_ZONE"
        assert d["status"] == "ACTIVE"
        assert len(d["components"]) == 3
        # Exactly one primary in dict
        primaries = [c for c in d["components"] if c["is_primary"]]
        assert len(primaries) == 1
