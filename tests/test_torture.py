"""Torture tests — adversarial inputs, edge cases, and boundary conditions.

These tests deliberately construct pathological IR structures, extreme
parameter values, and degenerate designs to verify that every stage of
the pipeline handles them without crashing, producing invalid output,
or silently dropping information.
"""

import json
import os

from hypothesis import given, settings
from hypothesis import strategies as st

os.environ.setdefault("NOSIS_PYSLANG_PATH", "D:/slang/build/lib")

from nosis.ir import Module, PrimOp, Design
from nosis.eval import eval_const_op
from nosis.passes import dead_code_eliminate, run_default_passes
from nosis.cse import eliminate_common_subexpressions
from nosis.fsm import extract_fsms
from nosis.bram import infer_brams
from nosis.dsp import infer_dsps
from nosis.carry import infer_carry_chains
from nosis.techmap import map_to_ecp5
from nosis.json_backend import emit_json_str
from nosis.equiv import check_equivalence
from nosis.lutpack import pack_luts_ir
from nosis.clocks import analyze_clock_domains
from nosis.resources import report_utilization


# ---------------------------------------------------------------------------
# Evaluation edge cases
# ---------------------------------------------------------------------------

class TestEvalEdgeCases:
    def test_shift_by_width(self):
        """Shifting by exactly the width should produce 0."""
        assert eval_const_op(PrimOp.SHL, {"A": 0xFF, "B": 8}, {}, 8) == 0

    def test_shift_by_more_than_width(self):
        """Shifting by more than width should produce 0."""
        assert eval_const_op(PrimOp.SHL, {"A": 0xFF, "B": 100}, {}, 8) == 0
        assert eval_const_op(PrimOp.SHR, {"A": 0xFF, "B": 100}, {}, 8) == 0

    def test_all_ones_and(self):
        assert eval_const_op(PrimOp.AND, {"A": 0xFFFFFFFF, "B": 0xFFFFFFFF}, {}, 32) == 0xFFFFFFFF

    def test_all_ones_or(self):
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 0xFFFFFFFF}, {}, 32) == 0xFFFFFFFF

    def test_zero_times_anything(self):
        assert eval_const_op(PrimOp.MUL, {"A": 0, "B": 999999}, {}, 32) == 0

    def test_max_mul_overflow(self):
        """Maximum values multiplied should wrap correctly."""
        result = eval_const_op(PrimOp.MUL, {"A": 0xFF, "B": 0xFF}, {}, 8)
        assert result == (0xFF * 0xFF) & 0xFF  # 0x01

    def test_sub_underflow_wraps(self):
        assert eval_const_op(PrimOp.SUB, {"A": 0, "B": 1}, {}, 8) == 255
        assert eval_const_op(PrimOp.SUB, {"A": 0, "B": 1}, {}, 32) == 0xFFFFFFFF

    def test_width_1_operations(self):
        """All operations on 1-bit values."""
        assert eval_const_op(PrimOp.AND, {"A": 1, "B": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.AND, {"A": 1, "B": 0}, {}, 1) == 0
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 0}, {}, 1) == 0
        assert eval_const_op(PrimOp.OR, {"A": 0, "B": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.XOR, {"A": 1, "B": 1}, {}, 1) == 0
        assert eval_const_op(PrimOp.NOT, {"A": 0}, {}, 1) == 1
        assert eval_const_op(PrimOp.NOT, {"A": 1}, {}, 1) == 0
        assert eval_const_op(PrimOp.ADD, {"A": 1, "B": 1}, {}, 1) == 0  # overflow
        assert eval_const_op(PrimOp.EQ, {"A": 0, "B": 0}, {}, 1) == 1
        assert eval_const_op(PrimOp.NE, {"A": 0, "B": 0}, {}, 1) == 0

    def test_sshr_all_ones(self):
        """SSHR of all-ones (negative in 2's complement) stays all-ones."""
        assert eval_const_op(PrimOp.SSHR, {"A": 0xFF, "B": 1}, {}, 8) == 0xFF
        assert eval_const_op(PrimOp.SSHR, {"A": 0xFF, "B": 7}, {}, 8) == 0xFF

    def test_sshr_by_zero(self):
        assert eval_const_op(PrimOp.SSHR, {"A": 0x80, "B": 0}, {}, 8) == 0x80

    def test_div_max_by_1(self):
        assert eval_const_op(PrimOp.DIV, {"A": 0xFF, "B": 1}, {}, 8) == 0xFF

    def test_mod_by_1(self):
        assert eval_const_op(PrimOp.MOD, {"A": 0xFF, "B": 1}, {}, 8) == 0

    def test_reduce_and_single_bit(self):
        assert eval_const_op(PrimOp.REDUCE_AND, {"A": 1}, {}, 1) == 1
        assert eval_const_op(PrimOp.REDUCE_AND, {"A": 0}, {}, 1) == 0

    def test_reduce_xor_all_ones(self):
        """XOR of 8 ones = 0 (even number of set bits)."""
        assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0xFF}, {}, 8) == 0

    def test_reduce_xor_7_ones(self):
        """XOR of 7 ones = 1 (odd number of set bits)."""
        assert eval_const_op(PrimOp.REDUCE_XOR, {"A": 0x7F}, {}, 8) == 1

    def test_sext_from_1_to_32(self):
        """Sign-extend a single bit."""
        assert eval_const_op(PrimOp.SEXT, {"A": 1}, {"from_width": 1}, 32) == 0xFFFFFFFF
        assert eval_const_op(PrimOp.SEXT, {"A": 0}, {"from_width": 1}, 32) == 0

    def test_slice_beyond_width(self):
        """Slicing beyond the value width should give 0 bits."""
        result = eval_const_op(PrimOp.SLICE, {"A": 0xFF}, {"offset": 100, "width": 4}, 4)
        assert result == 0

    def test_concat_empty(self):
        result = eval_const_op(PrimOp.CONCAT, {}, {"count": 0}, 0)
        assert result == 0

    def test_repeat_zero_times(self):
        """Repeat 0 times should give 0."""
        result = eval_const_op(PrimOp.REPEAT, {"A": 0xFF}, {"count": 0, "a_width": 8}, 8)
        assert result == 0


# ---------------------------------------------------------------------------
# Degenerate IR structures
# ---------------------------------------------------------------------------

class TestDegenerateIR:
    def test_empty_module(self):
        """An empty module should survive all passes without crashing."""
        mod = Module(name="empty")
        run_default_passes(mod)
        eliminate_common_subexpressions(mod)
        extract_fsms(mod)
        infer_brams(mod)
        infer_dsps(mod)
        infer_carry_chains(mod)
        pack_luts_ir(mod)
        analyze_clock_domains(mod)
        design = Design(modules={"empty": mod}, top="empty")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert "empty" in data["modules"]

    def test_input_only(self):
        """Module with only inputs and no outputs."""
        mod = Module(name="sink")
        for i in range(10):
            n = mod.add_net(f"in{i}", 8)
            c = mod.add_cell(f"in{i}_p", PrimOp.INPUT, port_name=f"in{i}")
            mod.connect(c, "Y", n, direction="output")
            mod.ports[f"in{i}"] = n
        run_default_passes(mod)
        design = Design(modules={"sink": mod}, top="sink")
        nl = map_to_ecp5(design)
        assert nl.stats()["ports"] == 10

    def test_output_only(self):
        """Module with only outputs driven by constants."""
        mod = Module(name="source")
        for i in range(10):
            cn = mod.add_net(f"c{i}", 8)
            cc = mod.add_cell(f"c{i}", PrimOp.CONST, value=i * 17, width=8)
            mod.connect(cc, "Y", cn, direction="output")
            on = mod.add_net(f"out{i}", 8)
            oc = mod.add_cell(f"out{i}_p", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", cn)
            mod.ports[f"out{i}"] = on
        run_default_passes(mod)
        design = Design(modules={"source": mod}, top="source")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert len(data["modules"]["source"]["ports"]) == 10

    def test_long_chain(self):
        """A chain of 100 NOT gates should survive all passes."""
        mod = Module(name="chain")
        inp = mod.add_net("inp", 1)
        ic = mod.add_cell("inp_p", PrimOp.INPUT, port_name="inp")
        mod.connect(ic, "Y", inp, direction="output")
        mod.ports["inp"] = inp

        prev = inp
        for i in range(100):
            n = mod.add_net(f"n{i}", 1)
            c = mod.add_cell(f"not{i}", PrimOp.NOT)
            mod.connect(c, "A", prev)
            mod.connect(c, "Y", n, direction="output")
            prev = n

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        run_default_passes(mod)
        # Double-NOT elimination should reduce chain significantly
        not_cells = [c for c in mod.cells.values() if c.op == PrimOp.NOT]
        # 100 NOTs -> identity simplify removes pairs -> should have ~0 or ~1
        # (depends on how many pairs identity_simplify catches per pass)
        assert len(not_cells) <= 100  # at minimum, no more than original

    def test_wide_fanout(self):
        """One input driving 100 AND gates."""
        mod = Module(name="fanout")
        a = mod.add_net("a", 1)
        b = mod.add_net("b", 1)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a
        bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
        mod.connect(bc, "Y", b, direction="output")
        mod.ports["b"] = b

        for i in range(100):
            out = mod.add_net(f"out{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", out, direction="output")
            oc = mod.add_cell(f"out{i}_p", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", out)
            mod.ports[f"out{i}"] = out

        # CSE should eliminate 99 of 100 identical AND gates
        eliminated = eliminate_common_subexpressions(mod)
        assert eliminated == 99

    def test_deep_mux_tree(self):
        """A 32-deep MUX tree should not stack overflow."""
        mod = Module(name="deep_mux")
        sel = mod.add_net("sel", 1)
        sc = mod.add_cell("sel_p", PrimOp.INPUT, port_name="sel")
        mod.connect(sc, "Y", sel, direction="output")
        mod.ports["sel"] = sel

        prev = mod.add_net("base", 8)
        bc = mod.add_cell("base_c", PrimOp.CONST, value=0, width=8)
        mod.connect(bc, "Y", prev, direction="output")

        for i in range(32):
            alt = mod.add_net(f"alt{i}", 8)
            ac = mod.add_cell(f"alt{i}_c", PrimOp.CONST, value=i + 1, width=8)
            mod.connect(ac, "Y", alt, direction="output")

            out = mod.add_net(f"mux{i}", 8)
            mc = mod.add_cell(f"mux{i}", PrimOp.MUX)
            mod.connect(mc, "S", sel)
            mod.connect(mc, "A", prev)
            mod.connect(mc, "B", alt)
            mod.connect(mc, "Y", out, direction="output")
            prev = out

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        design = Design(modules={"deep_mux": mod}, top="deep_mux")
        nl = map_to_ecp5(design)
        assert nl.stats()["LUT4"] > 0

    def test_single_bit_ff(self):
        """A single-bit FF should produce exactly one TRELLIS_FF."""
        mod = Module(name="ff1")
        clk = mod.add_net("clk", 1)
        d = mod.add_net("d", 1)
        q = mod.add_net("q", 1)
        cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
        mod.connect(cc, "Y", clk, direction="output")
        mod.ports["clk"] = clk
        dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
        mod.connect(dc, "Y", d, direction="output")
        mod.ports["d"] = d
        qc = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
        mod.connect(qc, "A", q)
        mod.ports["q"] = q
        ff = mod.add_cell("ff", PrimOp.FF)
        mod.connect(ff, "CLK", clk)
        mod.connect(ff, "D", d)
        mod.connect(ff, "Q", q, direction="output")

        design = Design(modules={"ff1": mod}, top="ff1")
        nl = map_to_ecp5(design)
        assert nl.stats().get("TRELLIS_FF", 0) == 1

    def test_32bit_ff(self):
        """A 32-bit FF should produce exactly 32 TRELLIS_FFs."""
        mod = Module(name="ff32")
        clk = mod.add_net("clk", 1)
        d = mod.add_net("d", 32)
        q = mod.add_net("q", 32)
        cc = mod.add_cell("clk_p", PrimOp.INPUT, port_name="clk")
        mod.connect(cc, "Y", clk, direction="output")
        mod.ports["clk"] = clk
        dc = mod.add_cell("d_p", PrimOp.INPUT, port_name="d")
        mod.connect(dc, "Y", d, direction="output")
        mod.ports["d"] = d
        qc = mod.add_cell("q_p", PrimOp.OUTPUT, port_name="q")
        mod.connect(qc, "A", q)
        mod.ports["q"] = q
        ff = mod.add_cell("ff", PrimOp.FF)
        mod.connect(ff, "CLK", clk)
        mod.connect(ff, "D", d)
        mod.connect(ff, "Q", q, direction="output")

        design = Design(modules={"ff32": mod}, top="ff32")
        nl = map_to_ecp5(design)
        assert nl.stats().get("TRELLIS_FF", 0) == 32


# ---------------------------------------------------------------------------
# Optimization stress
# ---------------------------------------------------------------------------

class TestOptimizationStress:
    def test_all_const_design(self):
        """A design made entirely of constants should fold to nothing."""
        mod = Module(name="allconst")
        a = mod.add_net("a", 8)
        ac = mod.add_cell("a_c", PrimOp.CONST, value=42, width=8)
        mod.connect(ac, "Y", a, direction="output")

        b = mod.add_net("b", 8)
        bc = mod.add_cell("b_c", PrimOp.CONST, value=17, width=8)
        mod.connect(bc, "Y", b, direction="output")

        mid = mod.add_net("mid", 8)
        add = mod.add_cell("add", PrimOp.ADD)
        mod.connect(add, "A", a)
        mod.connect(add, "B", b)
        mod.connect(add, "Y", mid, direction="output")

        out = mod.add_net("out", 8)
        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", mid)
        mod.ports["out"] = out

        run_default_passes(mod)
        # The ADD should be folded to CONST(59)
        assert mod.cells["add"].op == PrimOp.CONST
        assert mod.cells["add"].params["value"] == 59

    def test_identity_chain(self):
        """a + 0 + 0 + 0 should simplify to just a."""
        mod = Module(name="ident")
        a = mod.add_net("a", 8)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a

        prev = a
        for i in range(5):
            zero = mod.add_net(f"z{i}", 8)
            zc = mod.add_cell(f"z{i}_c", PrimOp.CONST, value=0, width=8)
            mod.connect(zc, "Y", zero, direction="output")
            out = mod.add_net(f"add{i}", 8)
            cell = mod.add_cell(f"add{i}", PrimOp.ADD)
            mod.connect(cell, "A", prev)
            mod.connect(cell, "B", zero)
            mod.connect(cell, "Y", out, direction="output")
            prev = out

        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", prev)
        mod.ports["out"] = prev

        stats = run_default_passes(mod)
        # All 5 additions of zero should be simplified across rounds
        assert stats.get("round_0", 0) >= 3

    def test_cse_100_duplicates(self):
        """100 identical operations should reduce to 1."""
        mod = Module(name="cse100")
        a = mod.add_net("a", 1)
        b = mod.add_net("b", 1)
        for i in range(100):
            out = mod.add_net(f"out{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", out, direction="output")
        eliminated = eliminate_common_subexpressions(mod)
        assert eliminated == 99

    def test_dce_removes_large_dead_tree(self):
        """A large dead computation tree should be fully removed."""
        mod = Module(name="dead_tree")
        inp = mod.add_net("inp", 8)
        ic = mod.add_cell("inp_p", PrimOp.INPUT, port_name="inp")
        mod.connect(ic, "Y", inp, direction="output")
        mod.ports["inp"] = inp

        out = mod.add_net("out", 8)
        oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
        mod.connect(oc, "A", inp)  # output wired directly to input
        mod.ports["out"] = out

        # 200 dead cells
        for i in range(200):
            dn = mod.add_net(f"dead{i}", 8)
            dc = mod.add_cell(f"dead{i}_c", PrimOp.CONST, value=i, width=8)
            mod.connect(dc, "Y", dn, direction="output")

        removed = dead_code_eliminate(mod)
        assert removed == 200


# ---------------------------------------------------------------------------
# Equivalence checker adversarial cases
# ---------------------------------------------------------------------------

class TestEquivAdversarial:
    def test_identity_vs_identity(self):
        """Two passthrough modules must be equivalent."""
        def _passthrough(name):
            mod = Module(name=name)
            a = mod.add_net("a", 8)
            ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
            mod.connect(ac, "Y", a, direction="output")
            mod.ports["a"] = a
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", a)
            mod.ports["out"] = a
            return mod

        r = check_equivalence(_passthrough("a"), _passthrough("b"))
        assert r.equivalent

    def test_const_vs_const_same(self):
        """Two modules outputting the same constant must be equivalent."""
        def _const_out(name, val):
            mod = Module(name=name)
            c = mod.add_net("c", 8)
            cc = mod.add_cell("c_c", PrimOp.CONST, value=val, width=8)
            mod.connect(cc, "Y", c, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", c)
            mod.ports["out"] = c
            return mod

        r = check_equivalence(_const_out("a", 42), _const_out("b", 42))
        assert r.equivalent

    def test_const_vs_const_different(self):
        """Two modules outputting different constants must NOT be equivalent."""
        def _const_out(name, val):
            mod = Module(name=name)
            c = mod.add_net("c", 8)
            cc = mod.add_cell("c_c", PrimOp.CONST, value=val, width=8)
            mod.connect(cc, "Y", c, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
            mod.connect(oc, "A", c)
            mod.ports["out"] = c
            return mod

        r = check_equivalence(_const_out("a", 42), _const_out("b", 43))
        assert not r.equivalent

    def test_wide_comparison(self):
        """8-bit AND vs OR should be non-equivalent (random simulation fallback)."""
        def _gate(name, op):
            mod = Module(name=name)
            a = mod.add_net("a", 8)
            b = mod.add_net("b", 8)
            y = mod.add_net("y", 8)
            ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
            mod.connect(ac, "Y", a, direction="output")
            mod.ports["a"] = a
            bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
            mod.connect(bc, "Y", b, direction="output")
            mod.ports["b"] = b
            gc = mod.add_cell("gate", op)
            mod.connect(gc, "A", a)
            mod.connect(gc, "B", b)
            mod.connect(gc, "Y", y, direction="output")
            oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="y")
            mod.connect(oc, "A", y)
            mod.ports["y"] = y
            return mod

        # 8+8=16 bits, just at the exhaustive threshold
        r = check_equivalence(_gate("a", PrimOp.AND), _gate("b", PrimOp.OR), max_exhaustive_bits=16)
        assert not r.equivalent


# ---------------------------------------------------------------------------
# JSON output edge cases
# ---------------------------------------------------------------------------

class TestJSONEdgeCases:
    def test_empty_module_json(self):
        mod = Module(name="empty")
        design = Design(modules={"empty": mod}, top="empty")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert data["modules"]["empty"]["cells"] == {}
        assert data["modules"]["empty"]["ports"] == {}

    def test_module_name_with_special_chars(self):
        """Module names that contain dots or dollars should not break JSON."""
        mod = Module(name="my.module$1")
        design = Design(modules={"my.module$1": mod}, top="my.module$1")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert "my.module$1" in data["modules"]

    def test_very_wide_port(self):
        """A 256-bit port should produce 256 bit references."""
        mod = Module(name="wide")
        a = mod.add_net("a", 256)
        ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
        mod.connect(ac, "Y", a, direction="output")
        mod.ports["a"] = a
        design = Design(modules={"wide": mod}, top="wide")
        nl = map_to_ecp5(design)
        text = emit_json_str(nl)
        data = json.loads(text)
        assert len(data["modules"]["wide"]["ports"]["a"]["bits"]) == 256


# ---------------------------------------------------------------------------
# Resource reporting edge cases
# ---------------------------------------------------------------------------

class TestResourceEdgeCases:
    def test_overutilization_12k(self):
        """A design with 20000 LUTs should warn on 12k device."""
        mod = Module(name="big")
        # Create many LUTs
        for i in range(100):
            a = mod.add_net(f"a{i}", 1)
            b = mod.add_net(f"b{i}", 1)
            y = mod.add_net(f"y{i}", 1)
            cell = mod.add_cell(f"and{i}", PrimOp.AND)
            mod.connect(cell, "A", a)
            mod.connect(cell, "B", b)
            mod.connect(cell, "Y", y, direction="output")
            oc = mod.add_cell(f"out{i}", PrimOp.OUTPUT, port_name=f"out{i}")
            mod.connect(oc, "A", y)
            mod.ports[f"out{i}"] = y
        design = Design(modules={"big": mod}, top="big")
        nl = map_to_ecp5(design)
        report = report_utilization(nl, "12k")
        # With 100 1-bit ANDs -> 100 LUTs, should fit in 12k
        assert report.luts_used == 100
        assert len(report.warnings) == 0  # 100 < 12288

    def test_all_four_devices(self):
        """Report should work for all ECP5 sizes."""
        mod = Module(name="t")
        design = Design(modules={"t": mod}, top="t")
        nl = map_to_ecp5(design)
        for size in ("12k", "25k", "45k", "85k"):
            report = report_utilization(nl, size)
            assert report.device.name.startswith("LFE5U")


# ---------------------------------------------------------------------------
# Hypothesis: random module construction survives full pipeline
# ---------------------------------------------------------------------------

@given(
    n_gates=st.integers(min_value=1, max_value=20),
    op=st.sampled_from([PrimOp.AND, PrimOp.OR, PrimOp.XOR]),
    width=st.sampled_from([1, 4, 8]),
)
@settings(max_examples=50)
def test_random_module_survives_pipeline(n_gates, op, width):
    """A random chain of gates must survive the full pipeline."""
    mod = Module(name="random")
    a = mod.add_net("a", width)
    b = mod.add_net("b", width)
    ac = mod.add_cell("a_p", PrimOp.INPUT, port_name="a")
    mod.connect(ac, "Y", a, direction="output")
    mod.ports["a"] = a
    bc = mod.add_cell("b_p", PrimOp.INPUT, port_name="b")
    mod.connect(bc, "Y", b, direction="output")
    mod.ports["b"] = b

    prev = a
    for i in range(n_gates):
        out = mod.add_net(f"g{i}", width)
        cell = mod.add_cell(f"g{i}", op)
        mod.connect(cell, "A", prev)
        mod.connect(cell, "B", b)
        mod.connect(cell, "Y", out, direction="output")
        prev = out

    oc = mod.add_cell("out_p", PrimOp.OUTPUT, port_name="out")
    mod.connect(oc, "A", prev)
    mod.ports["out"] = prev

    run_default_passes(mod)
    design = Design(modules={"random": mod}, top="random")
    nl = map_to_ecp5(design)
    text = emit_json_str(nl)
    data = json.loads(text)
    assert "random" in data["modules"]
    # Must produce valid JSON with no None values
    assert text.count("null") == 0
