"""Tests for nosis.hierarchy — vendor primitive detection."""

from nosis.hierarchy import is_vendor_primitive, ECP5_BLACKBOX_NAMES


def test_usrmclk_is_primitive():
    assert is_vendor_primitive("USRMCLK")


def test_ehxplll_is_primitive():
    assert is_vendor_primitive("EHXPLLL")


def test_custom_module_is_not():
    assert not is_vendor_primitive("my_custom_module")


def test_all_known_primitives():
    """Every name in the blackbox set should be recognized."""
    for name in ECP5_BLACKBOX_NAMES:
        assert is_vendor_primitive(name), f"{name} not recognized"


def test_count():
    """Should have a substantial number of vendor primitives."""
    assert len(ECP5_BLACKBOX_NAMES) >= 30
