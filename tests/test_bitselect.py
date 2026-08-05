"""Bit-select lowering and optimization must preserve the selected bit.

Two defects motivated these tests.

The optimizer's functional-identity pass drove each input of a candidate cell
with 0 or 1 and read the identity off the resulting truth table. For
``SLICE(a, offset=0, width=1)`` with a wide ``a`` that probe sees bit 0 track
the whole net, so the slice was declared an identity and the one-bit output was
wired straight to the eight-bit source. Offsets above 0 survived, which made the
failure look index-specific.

Continuous assignment to a bit-select lowered its target as a read rather than a
write, so ``assign v[i] = e;`` produced a dangling slice and left ``v`` with no
driver at all.
"""

import tempfile
from pathlib import Path

from nosis.frontend import parse_files, lower_to_ir
from nosis.ir import PrimOp
from nosis.passes.pipeline import run_default_passes


def _lower(source: str, top: str, optimize: bool = True):
    src = tempfile.NamedTemporaryFile(suffix=".sv", mode="w", delete=False,
                                      encoding="utf-8")
    src.write(source)
    src.close()
    try:
        design = lower_to_ir(parse_files([src.name], top=top), top=top)
        mod = design.top_module()
        if optimize:
            run_default_passes(mod)
        return mod
    finally:
        Path(src.name).unlink()


BIT_READS = """\
module bitread(input [7:0] a, output b0, output b1, output b7);
    assign b0 = a[0];
    assign b1 = a[1];
    assign b7 = a[7];
endmodule
"""


def _slices(mod):
    return {int(c.params.get("offset", 0)): c
            for c in mod.cells.values() if c.op == PrimOp.SLICE}


def test_bit_zero_read_survives_optimization():
    """The slice at offset 0 must not be mistaken for the whole net."""
    mod = _lower(BIT_READS, "bitread")
    offsets = set(_slices(mod))
    assert {0, 1, 7} <= offsets, f"offset 0 was eliminated; kept {sorted(offsets)}"


def test_bit_zero_read_keeps_its_width():
    mod = _lower(BIT_READS, "bitread")
    for offset, cell in _slices(mod).items():
        assert int(cell.params.get("width", 0)) == 1, f"offset {offset} widened"


def test_bit_zero_output_port_stays_one_bit():
    mod = _lower(BIT_READS, "bitread")
    for name in ("b0", "b1", "b7"):
        net = mod.nets.get(name)
        if net is not None:
            assert net.width == 1, f"port {name} widened to {net.width}"


BIT_WRITES = """\
module bitwrite(input [3:0] a, output [3:0] v);
    assign v[0] = a[3];
    assign v[1] = a[2];
    assign v[2] = a[1];
    assign v[3] = a[0];
endmodule
"""


def test_continuous_bit_select_target_drives_the_net():
    """`assign v[i] = e` must give `v` a driver rather than a dangling slice."""
    mod = _lower(BIT_WRITES, "bitwrite", optimize=False)
    v = mod.nets.get("v")
    assert v is not None, "target net was never created"
    assert v.driver is not None, "assign v[i] left v undriven"


def test_continuous_bit_select_assembles_every_bit():
    mod = _lower(BIT_WRITES, "bitwrite", optimize=False)
    driver = mod.nets["v"].driver
    assert driver.op == PrimOp.CONCAT, f"v is driven by {driver.op}, not a concat"
    assert len(driver.inputs) == 4, f"only {len(driver.inputs)} of 4 bits assembled"


def test_continuous_bit_select_preserves_bit_order():
    """v[0] takes a[3], so the concat's first piece must trace back to bit 3."""
    mod = _lower(BIT_WRITES, "bitwrite", optimize=False)
    driver = mod.nets["v"].driver
    expected = [3, 2, 1, 0]
    for i, want in enumerate(expected):
        piece = driver.inputs[f"I{i}"]
        src = piece.driver
        assert src is not None and src.op == PrimOp.SLICE, \
            f"piece I{i} is not a slice of the input"
        assert int(src.params.get("offset", -1)) == want, \
            f"v[{i}] reads a[{src.params.get('offset')}], expected a[{want}]"
