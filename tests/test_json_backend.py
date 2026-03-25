"""Tests for nosis.json_backend — nextpnr JSON output."""

import json

from nosis.ir import Design, PrimOp
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str


def _simple_and_design():
    design = Design()
    mod = design.add_module("test_and")
    design.top = "test_and"

    a = mod.add_net("a", 1)
    a_cell = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(a_cell, "Y", a, direction="output")
    mod.ports["a"] = a

    b = mod.add_net("b", 1)
    b_cell = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(b_cell, "Y", b, direction="output")
    mod.ports["b"] = b

    y = mod.add_net("y", 1)
    y_cell = mod.add_cell("y_p", PrimOp.OUTPUT, port_name="y")
    mod.connect(y_cell, "A", y)
    mod.ports["y"] = y

    and_cell = mod.add_cell("and0", PrimOp.AND)
    mod.connect(and_cell, "A", a)
    mod.connect(and_cell, "B", b)
    mod.connect(and_cell, "Y", y, direction="output")

    return design


def test_json_valid():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    text = emit_json_str(nl)
    data = json.loads(text)
    assert "creator" in data
    assert "nosis" in data["creator"]
    assert "modules" in data


def test_json_has_module():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    assert "test_and" in data["modules"]
    mod = data["modules"]["test_and"]
    assert "ports" in mod
    assert "cells" in mod
    assert "netnames" in mod


def test_json_ports():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    assert "a" in mod["ports"]
    assert "b" in mod["ports"]
    assert "y" in mod["ports"]
    assert mod["ports"]["a"]["direction"] == "input"
    assert mod["ports"]["y"]["direction"] == "output"


def test_json_cells():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    cells = mod["cells"]
    # Should have at least one TRELLIS_SLICE
    slice_cells = [c for c in cells.values() if c["type"] == "LUT4"]
    assert len(slice_cells) >= 1
    cell = slice_cells[0]
    assert "INIT" in cell["parameters"]
    assert "connections" in cell
    assert "port_directions" in cell


def test_json_bit_numbering():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    # All port bits should be integers >= 0
    for port in mod["ports"].values():
        for bit in port["bits"]:
            assert isinstance(bit, int)
            assert bit >= 0
    # All cell connection bits should be integers (signals) or string constants
    for cell in mod["cells"].values():
        for port_bits in cell["connections"].values():
            for bit in port_bits:
                assert isinstance(bit, int) or (isinstance(bit, str) and bit in ("0", "1", "x"))


def test_json_top_attribute():
    design = _simple_and_design()
    nl = map_to_ecp5(design)
    data = json.loads(emit_json_str(nl))
    mod = data["modules"]["test_and"]
    assert mod["attributes"]["top"] == "00000000000000000000000000000001"
