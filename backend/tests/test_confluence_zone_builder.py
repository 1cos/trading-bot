"""Tests for confluence_zone_builder — COMPOSITE_CONFLUENCE_ZONE.

Spec reference: MAXBOT_RETEST_ENTRY_AND_TRADE_MANAGEMENT_SPEC.md §7.2.

Covers the full B7.2K test matrix (63 tests):
- ConfluenceZoneResult contract (R1–R20)
- Builder validation (V1–V14, V4b)
- Creation / non-creation (B1–B10, B5b, B8b)
- Anchor and chaining (C1)
- Ordering (D1–D5)
- Duplicates (E1–E4)
- CompositeZone construction (F1–F6)
"""

from __future__ import annotations

import math

import pytest

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent
from trading_lab.confluence_zone_builder import (
    ConfluenceZoneResult,
    build_confluence_zone,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _comp(
    price: float,
    source: str = "ORB_HIGH",
    width: float = 0.0,
    primary: bool = False,
) -> ZoneComponent:
    half = width / 2.0
    return ZoneComponent(
        source=source,
        price=price,
        lower_bound=price - half,
        upper_bound=price + half,
        is_primary=primary,
    )


def _primary(
    price: float,
    source: str = "ORB_HIGH",
    width: float = 0.0,
) -> ZoneComponent:
    return _comp(price, source=source, width=width, primary=True)


def _make_zone_2(
    p_price: float = 100.0,
    s_price: float = 100.05,
) -> CompositeZone:
    """Minimal valid CompositeZone with 2 components."""
    p = _primary(p_price)
    s = _comp(s_price)
    return CompositeZone(
        zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
        lower_bound=min(p.lower_bound, s.lower_bound),
        upper_bound=max(p.upper_bound, s.upper_bound),
        components=(p, s),
        status=ZoneStatus.ACTIVE,
    )


# ══════════════════════════════════════════════════════════════════════════════
# A. ConfluenceZoneResult contract — 20 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestResultValid:
    """R1–R2: valid construction."""

    def test_valid_with_zone(self):  # R1
        zone = _make_zone_2()
        r = ConfluenceZoneResult(zone=zone, unmerged=(), tolerance=0.10)
        assert r.zone is zone
        assert r.unmerged == ()
        assert r.tolerance == pytest.approx(0.10)

    def test_valid_with_none(self):  # R2
        c = _comp(100.0)
        r = ConfluenceZoneResult(zone=None, unmerged=(c,), tolerance=0.10)
        assert r.zone is None
        assert len(r.unmerged) == 1


class TestResultImmutability:
    """R3a–R3b: frozen and slots."""

    def test_frozen(self):  # R3a
        r = ConfluenceZoneResult(zone=None, unmerged=(), tolerance=0.10)
        with pytest.raises(AttributeError):
            r.zone = _make_zone_2()

    def test_slots(self):  # R3b
        r = ConfluenceZoneResult(zone=None, unmerged=(), tolerance=0.10)
        assert not hasattr(r, "__dict__")
        with pytest.raises(TypeError):
            r.new_attr = 1
        assert not hasattr(r, "new_attr")


class TestResultTypeValidation:
    """R4–R7: type checks on fields."""

    def test_zone_wrong_type(self):  # R4
        with pytest.raises(TypeError, match="CompositeZone or None"):
            ConfluenceZoneResult(zone="not_a_zone", unmerged=(), tolerance=0.1)

    def test_unmerged_not_tuple(self):  # R5
        with pytest.raises(TypeError, match="unmerged must be a tuple"):
            ConfluenceZoneResult(zone=None, unmerged=[_comp(1.0)], tolerance=0.1)

    def test_unmerged_element_wrong_type(self):  # R6
        with pytest.raises(TypeError, match="unmerged.*must be a ZoneComponent"):
            ConfluenceZoneResult(zone=None, unmerged=({"x": 1},), tolerance=0.1)


class TestResultToleranceValidation:
    """R7–R15: tolerance validation in result contract."""

    def test_tolerance_bool(self):  # R7
        with pytest.raises(TypeError, match="bool"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance=True)

    def test_tolerance_wrong_type(self):  # R8
        with pytest.raises(TypeError, match="tolerance must be a number"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance="0.1")

    def test_tolerance_int_normalized(self):  # R9
        r = ConfluenceZoneResult(zone=None, unmerged=(), tolerance=1)
        assert r.tolerance == 1.0
        # int is accepted and normalised to float via object.__setattr__
        assert isinstance(r.tolerance, float)

    def test_tolerance_nan(self):  # R10
        with pytest.raises(ValueError, match="finite"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance=float("nan"))

    def test_tolerance_pos_inf(self):  # R11
        with pytest.raises(ValueError, match="finite"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance=float("inf"))

    def test_tolerance_neg_inf(self):  # R12
        with pytest.raises(ValueError, match="finite"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance=float("-inf"))

    def test_tolerance_negative(self):  # R13
        with pytest.raises(ValueError, match=">= 0"):
            ConfluenceZoneResult(zone=None, unmerged=(), tolerance=-0.01)

    def test_tolerance_zero_accepted(self):  # R14
        r = ConfluenceZoneResult(zone=None, unmerged=(), tolerance=0.0)
        assert r.tolerance == 0.0


class TestResultZoneCardinalityAndOverlap:
    """R15–R20: zone cardinality and overlap checks."""

    def test_zone_one_component_rejected(self):  # R15
        p = _primary(100.0)
        zone = CompositeZone(
            zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
            lower_bound=100.0, upper_bound=100.0,
            components=(p,), status=ZoneStatus.ACTIVE,
        )
        with pytest.raises(ValueError, match="at least 2"):
            ConfluenceZoneResult(zone=zone, unmerged=(), tolerance=0.1)

    def test_overlap_same_instance(self):  # R16
        s = _comp(100.05)
        zone = _make_zone_2()
        with pytest.raises(ValueError, match="overlap"):
            ConfluenceZoneResult(
                zone=zone, unmerged=(zone.components[1],), tolerance=0.1,
            )

    def test_overlap_structural_equality(self):  # R17
        zone = _make_zone_2(100.0, 100.05)
        twin = _comp(100.05)  # structurally equal to zone.components[1]
        assert twin == zone.components[1]
        assert twin is not zone.components[1]
        with pytest.raises(ValueError, match="overlap"):
            ConfluenceZoneResult(zone=zone, unmerged=(twin,), tolerance=0.1)

    def test_duplicate_within_unmerged_accepted(self):  # R18
        c = _comp(100.0)
        r = ConfluenceZoneResult(zone=None, unmerged=(c, c), tolerance=0.1)
        assert len(r.unmerged) == 2

    def test_duplicate_within_zone_accepted(self):  # R19
        p = _primary(100.0)
        s1 = _comp(100.05)
        s2 = _comp(100.05)  # structurally equal to s1
        zone = CompositeZone(
            zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
            lower_bound=100.0, upper_bound=100.05,
            components=(p, s1, s2), status=ZoneStatus.ACTIVE,
        )
        r = ConfluenceZoneResult(zone=zone, unmerged=(), tolerance=0.1)
        assert len(r.zone.components) == 3


# ══════════════════════════════════════════════════════════════════════════════
# B. Builder validation — 15 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBuilderToleranceValidation:
    """V1–V6: tolerance validation in builder."""

    def test_tolerance_bool_rejected(self):  # V1
        with pytest.raises(TypeError, match="bool"):
            build_confluence_zone([_primary(100)], tolerance=True)

    def test_tolerance_wrong_type_rejected(self):  # V2
        with pytest.raises(TypeError, match="tolerance must be a number"):
            build_confluence_zone([_primary(100)], tolerance="x")

    def test_tolerance_nan_rejected(self):  # V3
        with pytest.raises(ValueError, match="finite"):
            build_confluence_zone([_primary(100)], tolerance=float("nan"))

    def test_tolerance_pos_inf_rejected(self):  # V4
        with pytest.raises(ValueError, match="finite"):
            build_confluence_zone([_primary(100)], tolerance=float("inf"))

    def test_tolerance_neg_inf_rejected(self):  # V4b
        with pytest.raises(ValueError, match="finite"):
            build_confluence_zone([_primary(100)], tolerance=float("-inf"))

    def test_tolerance_negative_rejected(self):  # V5
        with pytest.raises(ValueError, match=">= 0"):
            build_confluence_zone([_primary(100)], tolerance=-1.0)

    def test_tolerance_int_normalized(self):  # V6
        r = build_confluence_zone([_primary(100)], tolerance=1)
        assert isinstance(r.tolerance, float)
        assert r.tolerance == 1.0


class TestBuilderComponentsValidation:
    """V7–V14: components validation and precedence."""

    def test_components_not_list_rejected(self):  # V7
        with pytest.raises(TypeError, match="components must be a list"):
            build_confluence_zone((_primary(100),), tolerance=0.1)

    def test_tolerance_before_components(self):  # V8
        with pytest.raises(TypeError, match="bool"):
            build_confluence_zone(42, tolerance=True)

    def test_element_wrong_type(self):  # V9
        with pytest.raises(TypeError, match="must be a ZoneComponent"):
            build_confluence_zone([{"x": 1}], tolerance=0.1)

    def test_container_before_element(self):  # V10
        with pytest.raises(TypeError, match="components must be a list"):
            build_confluence_zone("string", tolerance=0.1)

    def test_empty_rejected(self):  # V11
        with pytest.raises(ValueError, match="must not be empty"):
            build_confluence_zone([], tolerance=0.1)

    def test_zero_primary_rejected(self):  # V12
        with pytest.raises(ValueError, match="found 0"):
            build_confluence_zone([_comp(100), _comp(200)], tolerance=0.1)

    def test_two_primary_rejected(self):  # V13
        with pytest.raises(ValueError, match="found 2"):
            build_confluence_zone([_primary(100), _primary(200)], tolerance=0.1)

    def test_element_before_primary(self):  # V14
        with pytest.raises(TypeError, match="must be a ZoneComponent"):
            build_confluence_zone([42, _primary(100)], tolerance=0.1)


# ══════════════════════════════════════════════════════════════════════════════
# C. Creation / non-creation — 12 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestCreationNonCreation:
    """B1–B10, B5b, B8b: zone creation logic."""

    def test_primary_only_no_zone(self):  # B1
        r = build_confluence_zone([_primary(100)], tolerance=0.10)
        assert r.zone is None
        assert len(r.unmerged) == 1
        assert r.unmerged[0].is_primary

    def test_primary_plus_one_included(self):  # B2
        r = build_confluence_zone(
            [_primary(100), _comp(100.05)], tolerance=0.10,
        )
        assert r.zone is not None
        assert len(r.zone.components) == 2
        assert len(r.unmerged) == 0

    def test_all_secondaries_excluded(self):  # B3
        r = build_confluence_zone(
            [_primary(100), _comp(200)], tolerance=0.10,
        )
        assert r.zone is None
        assert len(r.unmerged) == 2

    def test_boundary_included(self):  # B4
        r = build_confluence_zone(
            [_primary(100), _comp(100.10)], tolerance=0.10,
        )
        assert r.zone is not None

    def test_beyond_boundary_macroscopic(self):  # B5
        r = build_confluence_zone(
            [_primary(100), _comp(100.11)], tolerance=0.10,
        )
        assert r.zone is None

    def test_beyond_boundary_just_past_epsilon(self):  # B5b
        sec_price = 100.0 + 0.10 + 2e-12
        distance = abs(sec_price - 100.0)
        assert distance > 0.10 + 1e-12  # pre-condition

        r = build_confluence_zone(
            [_primary(100.0), _comp(sec_price)], tolerance=0.10,
        )
        assert r.zone is None

    def test_ieee754_boundary(self):  # B6
        # abs(222.38 - 222.22) = 0.1600...+epsilon in IEEE 754
        r = build_confluence_zone(
            [_primary(222.22), _comp(222.38)], tolerance=0.16,
        )
        assert r.zone is not None

    def test_tolerance_zero_identical_price(self):  # B7
        r = build_confluence_zone(
            [_primary(100), _comp(100)], tolerance=0.0,
        )
        assert r.zone is not None

    def test_tolerance_zero_below_epsilon(self):  # B8
        r = build_confluence_zone(
            [_primary(100), _comp(100 + 5e-13)], tolerance=0.0,
        )
        assert r.zone is not None

    def test_tolerance_zero_at_epsilon(self):  # B8b
        # Use 0.0 and 1e-12 to guarantee exact distance
        distance = abs(1e-12 - 0.0)
        assert distance == 1e-12  # pre-condition: exact

        r = build_confluence_zone(
            [_primary(0.0), _comp(1e-12)], tolerance=0.0,
        )
        assert r.zone is not None

    def test_tolerance_zero_beyond_epsilon(self):  # B9
        r = build_confluence_zone(
            [_primary(100), _comp(100 + 1.5e-12)], tolerance=0.0,
        )
        assert r.zone is None

    def test_mixed_included_excluded(self):  # B10
        r = build_confluence_zone(
            [_primary(100), _comp(100.05), _comp(200)], tolerance=0.10,
        )
        assert r.zone is not None
        assert len(r.zone.components) == 2
        assert len(r.unmerged) == 1
        assert r.unmerged[0].price == pytest.approx(200)


# ══════════════════════════════════════════════════════════════════════════════
# D. Anchor and chaining — 1 test
# ══════════════════════════════════════════════════════════════════════════════


class TestAnchorNoChaining:
    """C1: secondary close to another secondary but not to primary."""

    def test_no_chaining(self):  # C1
        r = build_confluence_zone(
            [_primary(100), _comp(100.9), _comp(101.8)],
            tolerance=1.0,
        )
        assert r.zone is not None
        zone_prices = [c.price for c in r.zone.components]
        assert pytest.approx(100.9) in zone_prices
        assert len(r.unmerged) == 1
        assert r.unmerged[0].price == pytest.approx(101.8)


# ══════════════════════════════════════════════════════════════════════════════
# E. Ordering — 5 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOrdering:
    """D1–D5: ordering of zone.components and unmerged."""

    def test_primary_first_in_zone(self):  # D1
        r = build_confluence_zone(
            [_comp(99), _primary(100), _comp(101)], tolerance=5.0,
        )
        assert r.zone.components[0].is_primary

    def test_secondaries_sorted_by_price(self):  # D2
        r = build_confluence_zone(
            [_primary(100), _comp(102), _comp(101)], tolerance=5.0,
        )
        sec = r.zone.components[1:]
        assert sec[0].price == pytest.approx(101)
        assert sec[1].price == pytest.approx(102)

    def test_equal_price_stable_order(self):  # D3
        r = build_confluence_zone(
            [_primary(100), _comp(101, source="B"), _comp(101, source="A")],
            tolerance=5.0,
        )
        sec = r.zone.components[1:]
        assert sec[0].source == "B"
        assert sec[1].source == "A"

    def test_unmerged_original_order(self):  # D4
        r = build_confluence_zone(
            [_primary(100), _comp(100.05), _comp(300), _comp(200)],
            tolerance=0.10,
        )
        assert r.zone is not None
        assert len(r.unmerged) == 2
        assert r.unmerged[0].price == pytest.approx(300)
        assert r.unmerged[1].price == pytest.approx(200)

    def test_primary_position_when_no_zone(self):  # D5
        r = build_confluence_zone(
            [_comp(50), _primary(100), _comp(150)], tolerance=0.01,
        )
        assert r.zone is None
        assert r.unmerged[0].price == pytest.approx(50)
        assert r.unmerged[1].price == pytest.approx(100)
        assert r.unmerged[1].is_primary
        assert r.unmerged[2].price == pytest.approx(150)


# ══════════════════════════════════════════════════════════════════════════════
# F. Duplicates — 4 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestDuplicates:
    """E1–E4: duplicate handling."""

    def test_same_instance_repeated(self):  # E1
        s = _comp(100.05)
        r = build_confluence_zone([_primary(100), s, s], tolerance=0.10)
        assert r.zone is not None
        assert len(r.zone.components) == 3

    def test_structural_equal_secondaries(self):  # E2
        r = build_confluence_zone(
            [_primary(100), _comp(100.05), _comp(100.05)], tolerance=0.10,
        )
        assert r.zone is not None
        assert len(r.zone.components) == 3

    def test_two_primary_identical_rejected(self):  # E3
        with pytest.raises(ValueError, match="found 2"):
            build_confluence_zone(
                [_primary(100), _primary(100)], tolerance=0.10,
            )

    def test_primary_and_secondary_same_price(self):  # E4
        r = build_confluence_zone(
            [_primary(100), _comp(100)], tolerance=0.10,
        )
        assert r.zone is not None
        assert len(r.zone.components) == 2
        assert r.zone.components[0].is_primary


# ══════════════════════════════════════════════════════════════════════════════
# G. CompositeZone construction — 6 tests
# ══════════════════════════════════════════════════════════════════════════════


class TestZoneConstruction:
    """F1–F6: properties of the produced CompositeZone."""

    def test_zone_type(self):  # F1
        r = build_confluence_zone(
            [_primary(100), _comp(100.05)], tolerance=0.10,
        )
        assert r.zone.zone_type == ZoneType.COMPOSITE_CONFLUENCE_ZONE

    def test_zone_status(self):  # F2
        r = build_confluence_zone(
            [_primary(100), _comp(100.05)], tolerance=0.10,
        )
        assert r.zone.status == ZoneStatus.ACTIVE

    def test_envelope_from_bounds(self):  # F3
        r = build_confluence_zone(
            [_primary(100, width=0.04), _comp(100.05, width=0.06)],
            tolerance=0.10,
        )
        assert r.zone.lower_bound == pytest.approx(99.98)
        assert r.zone.upper_bound == pytest.approx(100.08)

    def test_wide_bounds_included(self):  # F4
        r = build_confluence_zone(
            [_primary(100), _comp(100.5, width=10.0)], tolerance=1.0,
        )
        assert r.zone.lower_bound == pytest.approx(95.5)
        assert r.zone.upper_bound == pytest.approx(105.5)

    def test_tolerance_in_result(self):  # F5
        r = build_confluence_zone(
            [_primary(100), _comp(100.05)], tolerance=0.25,
        )
        assert r.tolerance == pytest.approx(0.25)

    def test_tolerance_int_in_result(self):  # F6
        r = build_confluence_zone(
            [_primary(100), _comp(100.5)], tolerance=1,
        )
        assert isinstance(r.tolerance, float)
        assert r.tolerance == 1.0
