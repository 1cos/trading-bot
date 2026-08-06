"""Tests for ZoneComponent and CompositeZone contracts.

Covers acceptance criteria from B1 preflight:
- Construction, immutability, validation, serialization.
- Primary level single-source-of-truth via property.
- Bounds envelope validation.
- Bool rejection, NaN/Inf rejection.
- ZoneStatus semantics.
"""

import math

import pytest

from trading_lab.contracts.enums import ZoneStatus, ZoneType
from trading_lab.contracts.zone import CompositeZone, ZoneComponent


# ── Helpers ───────────────────────────────────────────────────────────────────


def _line(source="PREVIOUS_DAY_HIGH", price=770.86, is_primary=False):
    """Build a line-type ZoneComponent (bounds == price)."""
    return ZoneComponent(
        source=source,
        price=price,
        lower_bound=price,
        upper_bound=price,
        is_primary=is_primary,
    )


def _band(
    source="ORB_HIGH",
    price=770.79,
    lower_bound=770.50,
    upper_bound=770.79,
    is_primary=True,
):
    """Build a band-type ZoneComponent."""
    return ZoneComponent(
        source=source,
        price=price,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        is_primary=is_primary,
    )


def _zone(**overrides):
    """Build a valid CompositeZone with defaults."""
    orb = _band(is_primary=True)
    pdh = _line(is_primary=False)
    defaults = dict(
        zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
        lower_bound=770.50,
        upper_bound=770.86,
        components=(orb, pdh),
        status=ZoneStatus.ACTIVE,
    )
    defaults.update(overrides)
    return CompositeZone(**defaults)


# ═══════════════════════════════════════════════════════════════════════════════
# ZoneComponent
# ═══════════════════════════════════════════════════════════════════════════════


class TestZoneComponentValid:
    def test_line(self):
        c = _line()
        assert c.source == "PREVIOUS_DAY_HIGH"
        assert c.price == 770.86
        assert c.lower_bound == 770.86
        assert c.upper_bound == 770.86
        assert c.is_primary is False

    def test_band(self):
        c = _band()
        assert c.lower_bound == 770.50
        assert c.upper_bound == 770.79
        assert c.price == 770.79

    def test_int_prices_normalized(self):
        c = ZoneComponent(
            source="TEST", price=100, lower_bound=99, upper_bound=101,
            is_primary=False,
        )
        assert isinstance(c.price, float)
        assert isinstance(c.lower_bound, float)
        assert isinstance(c.upper_bound, float)

    def test_frozen(self):
        c = _line()
        with pytest.raises(AttributeError):
            c.price = 999.0


class TestZoneComponentSerialization:
    def test_line_to_dict(self):
        d = _line(price=770.86).to_dict()
        assert d == {
            "source": "PREVIOUS_DAY_HIGH",
            "price": 770.86,
            "lower_bound": 770.86,
            "upper_bound": 770.86,
            "is_primary": False,
        }

    def test_band_to_dict(self):
        d = _band().to_dict()
        assert d == {
            "source": "ORB_HIGH",
            "price": 770.79,
            "lower_bound": 770.50,
            "upper_bound": 770.79,
            "is_primary": True,
        }


class TestZoneComponentValidation:
    def test_empty_source(self):
        with pytest.raises(ValueError, match="non-empty"):
            ZoneComponent(
                source="", price=1.0, lower_bound=1.0,
                upper_bound=1.0, is_primary=False,
            )

    def test_non_string_source(self):
        with pytest.raises(TypeError, match="str"):
            ZoneComponent(
                source=42, price=1.0, lower_bound=1.0,
                upper_bound=1.0, is_primary=False,
            )

    def test_lower_greater_than_upper(self):
        with pytest.raises(ValueError, match="<="):
            ZoneComponent(
                source="X", price=5.0, lower_bound=6.0,
                upper_bound=5.0, is_primary=False,
            )

    def test_price_below_lower_bound(self):
        """AC#23: price outside own bounds raises ValueError."""
        with pytest.raises(ValueError, match="within"):
            ZoneComponent(
                source="X", price=4.0, lower_bound=5.0,
                upper_bound=6.0, is_primary=False,
            )

    def test_price_above_upper_bound(self):
        """AC#23: price outside own bounds raises ValueError."""
        with pytest.raises(ValueError, match="within"):
            ZoneComponent(
                source="X", price=7.0, lower_bound=5.0,
                upper_bound=6.0, is_primary=False,
            )

    def test_bool_rejected_as_price(self):
        """AC#24."""
        with pytest.raises(TypeError, match="bool"):
            ZoneComponent(
                source="X", price=True, lower_bound=0.0,
                upper_bound=1.0, is_primary=False,
            )

    def test_bool_rejected_as_lower_bound(self):
        with pytest.raises(TypeError, match="bool"):
            ZoneComponent(
                source="X", price=1.0, lower_bound=False,
                upper_bound=1.0, is_primary=False,
            )

    def test_nan_rejected(self):
        """AC#26."""
        with pytest.raises(ValueError, match="finite"):
            ZoneComponent(
                source="X", price=float("nan"), lower_bound=1.0,
                upper_bound=2.0, is_primary=False,
            )

    def test_inf_rejected(self):
        with pytest.raises(ValueError, match="finite"):
            ZoneComponent(
                source="X", price=1.5, lower_bound=1.0,
                upper_bound=float("inf"), is_primary=False,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# CompositeZone
# ═══════════════════════════════════════════════════════════════════════════════


class TestCompositeZoneValid:
    def test_basic_construction(self):
        z = _zone()
        assert z.zone_type == ZoneType.COMPOSITE_CONFLUENCE_ZONE
        assert z.lower_bound == 770.50
        assert z.upper_bound == 770.86
        assert len(z.components) == 2
        assert z.status == ZoneStatus.ACTIVE

    def test_validated_pivot_zone(self):
        p1 = _line("PIVOT_WICK", 100.0, is_primary=True)
        p2 = _line("PIVOT_WICK", 100.05, is_primary=False)
        p3 = _line("PIVOT_WICK", 100.02, is_primary=False)
        z = CompositeZone(
            zone_type=ZoneType.VALIDATED_PIVOT_ZONE,
            lower_bound=100.0,
            upper_bound=100.05,
            components=(p1, p2, p3),
            status=ZoneStatus.SECONDARY,
        )
        assert z.zone_type == ZoneType.VALIDATED_PIVOT_ZONE
        assert z.status == ZoneStatus.SECONDARY
        assert len(z.components) == 3

    def test_single_component(self):
        c = _band(is_primary=True)
        z = CompositeZone(
            zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
            lower_bound=c.lower_bound,
            upper_bound=c.upper_bound,
            components=(c,),
            status=ZoneStatus.STORED,
        )
        assert len(z.components) == 1
        assert z.status == ZoneStatus.STORED

    def test_frozen(self):
        z = _zone()
        with pytest.raises(AttributeError):
            z.status = ZoneStatus.STORED


class TestCompositeZonePrimaryLevel:
    def test_primary_component_property(self):
        z = _zone()
        pc = z.primary_component
        assert pc.source == "ORB_HIGH"
        assert pc.is_primary is True

    def test_primary_level_price_property(self):
        z = _zone()
        assert z.primary_level_price == 770.79

    def test_primary_derived_not_stored(self):
        """Primary level comes from components, not a separate field."""
        z = _zone()
        assert not hasattr(z, "_primary_level_price")
        # The property recomputes from components
        assert z.primary_level_price == z.primary_component.price


class TestCompositeZoneSerialization:
    def test_to_dict(self):
        z = _zone()
        d = z.to_dict()
        assert d["zone_type"] == "COMPOSITE_CONFLUENCE_ZONE"
        assert d["lower_bound"] == 770.50
        assert d["upper_bound"] == 770.86
        assert d["status"] == "ACTIVE"
        assert isinstance(d["components"], list)
        assert len(d["components"]) == 2
        assert d["components"][0]["source"] == "ORB_HIGH"
        assert d["components"][0]["is_primary"] is True
        assert d["components"][1]["source"] == "PREVIOUS_DAY_HIGH"
        assert d["components"][1]["is_primary"] is False

    def test_enum_serialized_as_string(self):
        d = _zone().to_dict()
        assert isinstance(d["zone_type"], str)
        assert isinstance(d["status"], str)


class TestCompositeZoneValidation:
    def test_empty_components(self):
        with pytest.raises(ValueError, match="non-empty"):
            _zone(components=())

    def test_zero_primaries(self):
        c1 = _line("A", 1.0, is_primary=False)
        c2 = _line("B", 2.0, is_primary=False)
        with pytest.raises(ValueError, match="exactly one"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=1.0, upper_bound=2.0,
                components=(c1, c2),
                status=ZoneStatus.ACTIVE,
            )

    def test_two_primaries(self):
        c1 = _line("A", 1.0, is_primary=True)
        c2 = _line("B", 2.0, is_primary=True)
        with pytest.raises(ValueError, match="exactly one"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=1.0, upper_bound=2.0,
                components=(c1, c2),
                status=ZoneStatus.ACTIVE,
            )

    def test_bounds_mismatch_lower(self):
        """AC#22: bounds different from component envelope raises ValueError."""
        orb = _band(is_primary=True)  # lower=770.50
        pdh = _line(is_primary=False)  # 770.86
        with pytest.raises(ValueError, match="lower_bound"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=770.00,  # wrong
                upper_bound=770.86,
                components=(orb, pdh),
                status=ZoneStatus.ACTIVE,
            )

    def test_bounds_mismatch_upper(self):
        """AC#22."""
        orb = _band(is_primary=True)
        pdh = _line(is_primary=False)
        with pytest.raises(ValueError, match="upper_bound"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=770.50,
                upper_bound=771.00,  # wrong
                components=(orb, pdh),
                status=ZoneStatus.ACTIVE,
            )

    def test_bad_zone_type(self):
        with pytest.raises(TypeError, match="ZoneType"):
            _zone(zone_type="COMPOSITE_CONFLUENCE_ZONE")

    def test_bad_status_type(self):
        with pytest.raises(TypeError, match="ZoneStatus"):
            _zone(status="ACTIVE")

    def test_bad_component_type(self):
        with pytest.raises(TypeError, match="ZoneComponent"):
            _zone(
                components=({"source": "X"},),
                lower_bound=0, upper_bound=1,
            )

    def test_bounds_lower_gt_upper(self):
        c = _line("X", 5.0, is_primary=True)
        with pytest.raises(ValueError, match="<="):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=6.0, upper_bound=5.0,
                components=(c,),
                status=ZoneStatus.ACTIVE,
            )

    def test_bool_rejected_as_bound(self):
        """AC#24."""
        c = _line("X", 1.0, is_primary=True)
        with pytest.raises(TypeError, match="bool"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=True, upper_bound=1.0,
                components=(c,),
                status=ZoneStatus.ACTIVE,
            )

    def test_nan_rejected_in_bounds(self):
        """AC#26."""
        c = _line("X", 1.0, is_primary=True)
        with pytest.raises(ValueError, match="finite"):
            CompositeZone(
                zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
                lower_bound=float("nan"), upper_bound=1.0,
                components=(c,),
                status=ZoneStatus.ACTIVE,
            )


class TestZoneStatusSemantics:
    """AC#21: STORED and SECONDARY have distinct contractual semantics."""

    def test_all_three_statuses_exist(self):
        assert ZoneStatus.ACTIVE == "ACTIVE"
        assert ZoneStatus.SECONDARY == "SECONDARY"
        assert ZoneStatus.STORED == "STORED"

    def test_member_count(self):
        assert len(ZoneStatus) == 3

    def test_stored_and_secondary_are_distinct(self):
        assert ZoneStatus.STORED != ZoneStatus.SECONDARY

    def test_zone_with_stored_status(self):
        c = _line("X", 1.0, is_primary=True)
        z = CompositeZone(
            zone_type=ZoneType.VALIDATED_PIVOT_ZONE,
            lower_bound=1.0, upper_bound=1.0,
            components=(c,),
            status=ZoneStatus.STORED,
        )
        assert z.status == ZoneStatus.STORED

    def test_zone_with_secondary_status(self):
        c = _line("X", 1.0, is_primary=True)
        z = CompositeZone(
            zone_type=ZoneType.VALIDATED_PIVOT_ZONE,
            lower_bound=1.0, upper_bound=1.0,
            components=(c,),
            status=ZoneStatus.SECONDARY,
        )
        assert z.status == ZoneStatus.SECONDARY


class TestCompositeZoneEqualBounds:
    """Edge case: all components are lines at the same price."""

    def test_all_lines_same_price(self):
        c1 = _line("A", 100.0, is_primary=True)
        c2 = _line("B", 100.0, is_primary=False)
        z = CompositeZone(
            zone_type=ZoneType.COMPOSITE_CONFLUENCE_ZONE,
            lower_bound=100.0, upper_bound=100.0,
            components=(c1, c2),
            status=ZoneStatus.ACTIVE,
        )
        assert z.lower_bound == z.upper_bound == 100.0
