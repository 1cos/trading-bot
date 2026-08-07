"""Tests for pivot_cluster — VALIDATED_PIVOT_ZONE clustering.

Spec reference: MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.1.

Algorithm: iterative best-window extraction with spread <= tolerance.
No transitive chaining.  Each component belongs to at most one zone.
"""

from __future__ import annotations

import pytest

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import ZoneComponent
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

    width=0 → line (lower==upper==price).
    width>0 → band [price - width/2, price + width/2].
    """
    half = width / 2.0
    return ZoneComponent(
        source=source,
        price=price,
        lower_bound=price - half,
        upper_bound=price + half,
        is_primary=False,
    )


def _zone_prices(result: PivotClusterResult, zone_idx: int) -> list[float]:
    """Sorted list of component prices in a zone."""
    return sorted(c.price for c in result.zones[zone_idx].components)


def _unclustered_prices(result: PivotClusterResult) -> list[float]:
    """Sorted list of unclustered component prices."""
    return sorted(c.price for c in result.unclustered)


# ══════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC CASES — approved by Max
# ══════════════════════════════════════════════════════════════════════════════


class TestDiagnosticCases:
    """Mandatory diagnostic cases from the approved specification."""

    def test_three_within_tolerance(self):
        """100.00, 100.04, 100.08 tol=0.10 → 1 zone, 0 unclustered."""
        comps = [_make_comp(100.00), _make_comp(100.04), _make_comp(100.08)]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert len(r.unclustered) == 0
        assert _zone_prices(r, 0) == pytest.approx([100.00, 100.04, 100.08])

    def test_transitive_chaining_rejected(self):
        """100.00, 100.09, 100.18 tol=0.10 → 0 zones, 3 unclustered."""
        comps = [_make_comp(100.00), _make_comp(100.09), _make_comp(100.18)]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 0
        assert len(r.unclustered) == 3

    def test_four_components_best_three_win(self):
        """100.00, 100.04, 100.08, 100.12 tol=0.10
        → zone [100.00,100.04,100.08], unclustered [100.12]."""
        comps = [
            _make_comp(100.00), _make_comp(100.04),
            _make_comp(100.08), _make_comp(100.12),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert _zone_prices(r, 0) == pytest.approx([100.00, 100.04, 100.08])
        assert _unclustered_prices(r) == pytest.approx([100.12])

    def test_six_components_two_zones(self):
        """100.00,100.04,100.08,100.12,100.16,100.20 tol=0.10
        → 2 zones, 0 unclustered."""
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
            _make_comp(100.12), _make_comp(100.16), _make_comp(100.20),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 2
        assert len(r.unclustered) == 0
        all_prices = sorted(
            c.price for z in r.zones for c in z.components
        )
        assert all_prices == pytest.approx(
            [100.00, 100.04, 100.08, 100.12, 100.16, 100.20]
        )

    def test_two_distinct_groups(self):
        """100.00,100.03,100.06,100.50,100.53,100.56 tol=0.10
        → 2 zones, 0 unclustered."""
        comps = [
            _make_comp(100.00), _make_comp(100.03), _make_comp(100.06),
            _make_comp(100.50), _make_comp(100.53), _make_comp(100.56),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 2
        assert len(r.unclustered) == 0

    def test_residual_below_min_contacts(self):
        """100.00,100.04,100.08,100.15,100.19 tol=0.10
        → 1 zone, 2 unclustered."""
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
            _make_comp(100.15), _make_comp(100.19),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert _zone_prices(r, 0) == pytest.approx([100.00, 100.04, 100.08])
        assert len(r.unclustered) == 2
        assert _unclustered_prices(r) == pytest.approx([100.15, 100.19])


# ══════════════════════════════════════════════════════════════════════════════
# FLOATING-POINT PRECISION
# ══════════════════════════════════════════════════════════════════════════════


class TestFloatingPointPrecision:
    """Spread mathematically equal to tolerance must be included."""

    def test_spread_exactly_equals_tolerance(self):
        """Spread of 0.10 with tolerance 0.10 — must form a zone.

        100.10 and 100.20 differ by exactly 0.10 in real arithmetic,
        but IEEE 754 may represent the difference as 0.10 + epsilon.
        The comparison must handle this.
        """
        comps = [
            _make_comp(100.10), _make_comp(100.15), _make_comp(100.20),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert len(r.unclustered) == 0

    def test_spread_just_below_tolerance(self):
        """Spread clearly below tolerance."""
        comps = [
            _make_comp(100.00), _make_comp(100.03), _make_comp(100.06),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1

    def test_spread_clearly_above_tolerance(self):
        """Spread 0.18 with tolerance 0.10 — must NOT form a zone."""
        comps = [
            _make_comp(100.00), _make_comp(100.09), _make_comp(100.18),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 0

    def test_four_components_exact_boundary(self):
        """50.00–50.06 spread=0.06 with tolerance 0.06 — all four fit."""
        comps = [
            _make_comp(50.00), _make_comp(50.02),
            _make_comp(50.04), _make_comp(50.06),
        ]
        r = cluster_pivots(comps, tolerance=0.06)

        assert len(r.zones) == 1
        assert len(r.zones[0].components) == 4


# ══════════════════════════════════════════════════════════════════════════════
# WINDOW SELECTION CRITERIA
# ══════════════════════════════════════════════════════════════════════════════


class TestWindowSelection:
    """Best window selected by count > spread > start index."""

    def test_larger_window_wins_over_smaller(self):
        """4-component window beats any 3-component alternative."""
        comps = [
            _make_comp(100.00), _make_comp(100.03),
            _make_comp(100.06), _make_comp(100.09),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert len(r.zones[0].components) == 4

    def test_tie_break_by_start_index(self):
        """Equal count and spread → lowest start index wins.

        100.00, 100.04, 100.08, 100.12 tol=0.10:
        Two valid 3-windows with spread 0.08 each.
        [100.00,100.04,100.08] starts at index 0 — wins.
        """
        comps = [
            _make_comp(100.00), _make_comp(100.04),
            _make_comp(100.08), _make_comp(100.12),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert _zone_prices(r, 0) == pytest.approx([100.00, 100.04, 100.08])


# ══════════════════════════════════════════════════════════════════════════════
# TOO FEW CONTACTS
# ══════════════════════════════════════════════════════════════════════════════


class TestTooFewContacts:
    """Clusters with fewer than min_contacts are not grouped."""

    def test_two_pivots_not_grouped(self):
        comps = [_make_comp(100.00), _make_comp(100.05)]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 0
        assert len(r.unclustered) == 2

    def test_single_pivot_not_grouped(self):
        r = cluster_pivots([_make_comp(100.00)], tolerance=0.10)

        assert len(r.zones) == 0
        assert len(r.unclustered) == 1

    def test_empty_input(self):
        r = cluster_pivots([], tolerance=0.10)

        assert len(r.zones) == 0
        assert len(r.unclustered) == 0

    def test_custom_min_contacts(self):
        """min_contacts=2 should group two close pivots."""
        comps = [_make_comp(100.00), _make_comp(100.05)]
        r = cluster_pivots(comps, tolerance=0.10, min_contacts=2)

        assert len(r.zones) == 1


# ══════════════════════════════════════════════════════════════════════════════
# INPUT ORDER INDEPENDENCE
# ══════════════════════════════════════════════════════════════════════════════


class TestInputOrder:
    """Clustering produces the same zones regardless of input order."""

    def test_unsorted_input(self):
        comps = [_make_comp(100.08), _make_comp(100.00), _make_comp(100.04)]
        r = cluster_pivots(comps, tolerance=0.10)

        assert len(r.zones) == 1
        assert len(r.zones[0].components) == 3

    def test_reverse_order_same_result(self):
        forward = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r_fwd = cluster_pivots(forward, tolerance=0.10)
        r_rev = cluster_pivots(list(reversed(forward)), tolerance=0.10)

        assert _zone_prices(r_fwd, 0) == _zone_prices(r_rev, 0)


# ══════════════════════════════════════════════════════════════════════════════
# ZONE PROPERTIES
# ══════════════════════════════════════════════════════════════════════════════


class TestZoneProperties:
    """CompositeZone invariants, primary, envelope, sources."""

    def test_zone_type(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert r.zones[0].zone_type == ZoneType.VALIDATED_PIVOT_ZONE

    def test_default_status_active(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert r.zones[0].status == ZoneStatus.ACTIVE

    def test_explicit_status_propagated(self):
        """Caller-provided status overrides the default."""
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(
            comps, tolerance=0.10, status=ZoneStatus.STORED,
        )

        assert r.zones[0].status == ZoneStatus.STORED

    def test_explicit_status_secondary(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(
            comps, tolerance=0.10, status=ZoneStatus.SECONDARY,
        )

        assert r.zones[0].status == ZoneStatus.SECONDARY

    def test_exactly_one_primary(self):
        comps = [
            _make_comp(200.00), _make_comp(200.04), _make_comp(200.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        primaries = [c for c in r.zones[0].components if c.is_primary]
        assert len(primaries) == 1

    def test_primary_closest_to_median(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        primary = r.zones[0].primary_component
        assert primary.price == pytest.approx(100.04)

    def test_primary_level_price(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        assert r.zones[0].primary_level_price == pytest.approx(100.04)

    def test_envelope_matches_component_bounds(self):
        comps = [
            _make_comp(100.00, width=0.04),  # 99.98–100.02
            _make_comp(100.04, width=0.06),  # 100.01–100.07
            _make_comp(100.08, width=0.02),  # 100.07–100.09
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        zone = r.zones[0]
        assert zone.lower_bound == pytest.approx(99.98)
        assert zone.upper_bound == pytest.approx(100.09)

    def test_envelope_not_inflated_by_tolerance(self):
        """Zone width = component span, not tolerance."""
        comps = [
            _make_comp(100.00), _make_comp(100.03), _make_comp(100.06),
        ]
        r = cluster_pivots(comps, tolerance=0.50)

        zone = r.zones[0]
        assert zone.lower_bound == pytest.approx(100.00)
        assert zone.upper_bound == pytest.approx(100.06)

    def test_source_labels_preserved(self):
        comps = [
            _make_comp(100.00, source="PIVOT_WICK"),
            _make_comp(100.04, source="OB"),
            _make_comp(100.08, source="PIVOT_WICK"),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        sources = {c.source for c in r.zones[0].components}
        assert sources == {"PIVOT_WICK", "OB"}

    def test_zone_is_frozen(self):
        comps = [
            _make_comp(100.00), _make_comp(100.04), _make_comp(100.08),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        with pytest.raises(AttributeError):
            r.zones[0].status = ZoneStatus.STORED

    def test_to_dict_roundtrip(self):
        comps = [
            _make_comp(100.00, source="PIVOT_WICK"),
            _make_comp(100.04, source="OB"),
            _make_comp(100.08, source="PIVOT_WICK"),
        ]
        r = cluster_pivots(comps, tolerance=0.10)

        d = r.zones[0].to_dict()
        assert d["zone_type"] == "VALIDATED_PIVOT_ZONE"
        assert len(d["components"]) == 3
        primaries = [c for c in d["components"] if c["is_primary"]]
        assert len(primaries) == 1


# ══════════════════════════════════════════════════════════════════════════════
# RESULT METADATA
# ══════════════════════════════════════════════════════════════════════════════


class TestResultMetadata:
    def test_tolerance_recorded(self):
        r = cluster_pivots([], tolerance=0.25)
        assert r.tolerance == pytest.approx(0.25)

    def test_min_contacts_recorded(self):
        r = cluster_pivots([], tolerance=0.10, min_contacts=5)
        assert r.min_contacts == 5


# ══════════════════════════════════════════════════════════════════════════════
# PARAMETER VALIDATION
# ══════════════════════════════════════════════════════════════════════════════


class TestValidation:
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

    def test_status_wrong_type_raises(self):
        with pytest.raises(TypeError, match="status must be a ZoneStatus"):
            cluster_pivots([], tolerance=0.10, status="ACTIVE")
