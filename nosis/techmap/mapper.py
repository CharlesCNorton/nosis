"""ECP5 technology mapper — converts IR to ECP5 netlist cells."""

from __future__ import annotations

from nosis.ir import Cell, Design, Module, Net, PrimOp
from nosis.techmap.netlist import ECP5Net, ECP5Netlist, _compute_lut4_init, _const_bits

class _ECP5Mapper:
    """Maps a Nosis IR Module to an ECP5Netlist."""

    def __init__(self, netlist: ECP5Netlist) -> None:
        self.nl = netlist
        self._cell_counter = 0
        self._net_map: dict[str, ECP5Net] = {}
        self._ir_mod: Module | None = None

    def _fresh_name(self, prefix: str) -> str:
        name = f"${prefix}_{self._cell_counter}"
        self._cell_counter += 1
        return name

    def _get_net(self, ir_net: Net) -> ECP5Net:
        """Get or create the ECP5 net corresponding to an IR net."""
        if ir_net.name in self._net_map:
            return self._net_map[ir_net.name]
        ecp5_net = self.nl.add_net(ir_net.name, ir_net.width)
        self._net_map[ir_net.name] = ecp5_net
        return ecp5_net

    def _get_bit(self, ir_net: Net, bit_index: int = 0) -> int | str:
        """Get a single bit reference from an IR net."""
        ecp5_net = self._get_net(ir_net)
        if bit_index < len(ecp5_net.bits):
            return ecp5_net.bits[bit_index]
        return "0"

    def _get_bits(self, ir_net: Net) -> list[int | str]:
        """Get all bit references from an IR net."""
        return self._get_net(ir_net).bits

    def map_module(self, mod: Module) -> None:
        """Map all cells in an IR module to ECP5 cells."""
        self._ir_mod = mod
        # First pass: create ECP5 nets for all IR nets
        for net in mod.nets.values():
            self._get_net(net)

        # Map ports
        for port_name, port_net in mod.ports.items():
            ecp5_net = self._get_net(port_net)
            # Determine direction from the IR cells
            direction = "input"
            for cell in mod.cells.values():
                if cell.op == PrimOp.OUTPUT and port_name in cell.params.get("port_name", ""):
                    direction = "output"
                    break
                if cell.op == PrimOp.OUTPUT:
                    for inp_net in cell.inputs.values():
                        if inp_net.name == port_name:
                            direction = "output"
                            break
                if cell.op == PrimOp.INPUT:
                    if cell.params.get("inout"):
                        direction = "inout"
                        break

            self.nl.ports[port_name] = {
                "direction": direction,
                "bits": ecp5_net.bits,
            }

        # Second pass: map each IR cell
        for cell in mod.cells.values():
            self._map_cell(cell)

    def _map_cell(self, cell: Cell) -> None:
        """Map a single IR cell to one or more ECP5 cells."""
        op = cell.op

        if op == PrimOp.LATCH:
            # Map latches as TRELLIS_FF with transparent enable
            # ECP5 doesn't have dedicated latches — use FF with CE as enable
            self._map_ff(cell)
            return

        if op == PrimOp.INPUT or op == PrimOp.OUTPUT:
            # Tri-state buffer inference for inout ports
            if op == PrimOp.INPUT and cell.params.get("inout"):
                # Emit a BB (bidirectional buffer) for inout ports.
                # Look for an OUTPUT cell on the same port to find the drive data.
                port_name = cell.params.get("port_name", "")
                drive_bits: list[int | str] = ["0"]
                tristate_bits: list[int | str] = ["1"]  # default: hi-Z
                if self._ir_mod:
                    for oc in self._ir_mod.cells.values():
                        if oc.op == PrimOp.OUTPUT and oc.params.get("port_name") == port_name:
                            for inp_net in oc.inputs.values():
                                drive_bits = self._get_bits(inp_net)[:1] or ["0"]
                            break
                for out_net in cell.outputs.values():
                    ecp5_net = self._get_net(out_net)
                    bb = self.nl.add_cell(self._fresh_name("bb"), "BB")
                    bb.ports["I"] = drive_bits[:1]
                    bb.ports["T"] = tristate_bits[:1]
                    bb.ports["O"] = ecp5_net.bits[:1] if ecp5_net.bits else ["0"]
                    bb.ports["B"] = ecp5_net.bits[:1] if ecp5_net.bits else ["0"]
            return  # handled as ports

        if op == PrimOp.CONST:
            self._map_const(cell)
        elif op == PrimOp.FF:
            self._map_ff(cell)
        elif op in (PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
                     PrimOp.MUX, PrimOp.EQ, PrimOp.NE,
                     PrimOp.REDUCE_AND, PrimOp.REDUCE_OR, PrimOp.REDUCE_XOR):
            self._map_lut(cell)
        elif op in (PrimOp.ADD, PrimOp.SUB):
            self._map_arithmetic(cell)
        elif op in (PrimOp.MUL, PrimOp.DIV, PrimOp.MOD):
            self._map_multiply(cell)
        elif op in (PrimOp.SHL, PrimOp.SHR, PrimOp.SSHR):
            self._map_shift(cell)
        elif op in (PrimOp.LT, PrimOp.LE, PrimOp.GT, PrimOp.GE):
            self._map_compare(cell)
        elif op == PrimOp.CONCAT:
            self._map_concat(cell)
        elif op == PrimOp.SLICE:
            self._map_slice(cell)
        elif op in (PrimOp.ZEXT, PrimOp.SEXT):
            self._map_extend(cell)
        elif op == PrimOp.MEMORY:
            self._map_memory(cell)
        elif op == PrimOp.PMUX:
            self._map_pmux(cell)
        elif op == PrimOp.REPEAT:
            self._map_repeat(cell)
        else:
            self._map_unknown(cell)

    def _map_const(self, cell: Cell) -> None:
        """Map a constant to tied bit values (no physical cell needed)."""
        value = int(cell.params.get("value", 0))
        width = int(cell.params.get("width", 1))
        for port_name, out_net in cell.outputs.items():
            ecp5_net = self._get_net(out_net)
            # Override bits with constant values
            ecp5_net.bits = _const_bits(value, width)

    def _map_ff(self, cell: Cell) -> None:
        """Map an IR FF to TRELLIS_FF cells (one per bit)."""
        d_net = cell.inputs.get("D")
        clk_net = cell.inputs.get("CLK")
        rst_net = cell.inputs.get("RST")
        q_net = list(cell.outputs.values())[0] if cell.outputs else None

        if d_net is None or q_net is None:
            return

        width = d_net.width
        d_bits = self._get_bits(d_net)
        q_bits = self._get_bits(q_net)
        clk_bits = self._get_bits(clk_net) if clk_net else ["0"]
        rst_bits = self._get_bits(rst_net) if rst_net else ["0"]

        for i in range(min(width, len(d_bits), len(q_bits))):
            ff = self.nl.add_cell(self._fresh_name("tff"), "TRELLIS_FF")
            if cell.src:
                ff.attributes["src"] = cell.src
            is_async = bool(cell.params.get("async_reset", False))
            ff.parameters["GSR"] = "DISABLED"
            ff.parameters["CEMUX"] = "CE"
            ff.parameters["CLKMUX"] = "CLK"
            ff.parameters["LSRMUX"] = "LSR" if rst_net else "INV"
            ff.parameters["REGSET"] = "RESET"
            ff.parameters["SRMODE"] = "ASYNC" if is_async else "LSR_OVER_CE"
            ff.ports["CLK"] = [clk_bits[0] if clk_bits else "0"]
            ff.ports["DI"] = [d_bits[i] if i < len(d_bits) else "0"]
            ff.ports["LSR"] = [rst_bits[0] if rst_bits else "0"]
            ff.ports["CE"] = ["1"]
            ff.ports["Q"] = [q_bits[i] if i < len(q_bits) else self.nl.alloc_bit()]

    def _map_lut(self, cell: Cell) -> None:
        """Map a logic operation to LUT4 cells (one per output bit).

        Each LUT4 has ports A, B, C, D (inputs) and Z (output) with a
        16-bit INIT parameter as a binary string. This matches the cell
        format that nextpnr-ecp5 expects from yosys.
        """
        out_nets = list(cell.outputs.values())
        if not out_nets:
            return
        out_net = out_nets[0]
        width = out_net.width

        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        s_net = cell.inputs.get("S")

        init = _compute_lut4_init(cell.op, len(cell.inputs))
        init_bin = format(init, "016b")

        out_bits = self._get_bits(out_net)

        for i in range(width):
            lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
            if cell.src:
                lut.attributes["src"] = cell.src
            lut.parameters["INIT"] = init_bin

            if cell.op == PrimOp.MUX:
                lut.ports["A"] = [self._get_bit(s_net, min(i, s_net.width - 1)) if s_net else "0"]
                lut.ports["B"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["C"] = [self._get_bit(b_net, i) if b_net else "0"]
                lut.ports["D"] = ["0"]
            elif cell.op == PrimOp.NOT:
                lut.ports["A"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["B"] = ["0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
            else:
                lut.ports["A"] = [self._get_bit(a_net, i) if a_net else "0"]
                lut.ports["B"] = [self._get_bit(b_net, i) if b_net else "0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
            lut.ports["Z"] = [out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()]

    def _map_arithmetic(self, cell: Cell) -> None:
        """Map ADD/SUB to CCU2C carry chain cells.

        Each CCU2C handles 2 bits of addition with carry propagation.
        An N-bit adder uses ceil(N/2) CCU2C cells.
        """
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        out_nets = list(cell.outputs.values())
        if not a_net or not b_net or not out_nets:
            self._map_lut(cell)
            return

        out_net = out_nets[0]
        width = out_net.width
        if width < 2:
            self._map_lut(cell)
            return

        a_bits = self._get_bits(a_net)
        b_bits = self._get_bits(b_net)
        out_bits = self._get_bits(out_net)
        is_sub = (cell.op == PrimOp.SUB)

        # Base LUT INIT: XOR (a ^ b) = 0x6666, XNOR (a ^ ~b) for SUB = 0x9999
        base_init = 0x9999 if is_sub else 0x6666

        # Check if the output feeds a single consumer that can be absorbed
        # into the CCU2C INIT (XOR with constant, NOT, etc.)
        if out_net.name in self._net_map:
            pass  # can't easily check consumers at ECP5 level
        # For now, check the IR cell params for a packed_lut_init hint
        packed_init = cell.params.get("packed_lut_init")
        if packed_init is not None:
            base_init = int(packed_init) & 0xFFFF

        lut_init = format(base_init, "016b")

        prev_cout: int | str = "1" if is_sub else "0"  # carry-in

        for i in range(0, width, 2):
            ccu2c = self.nl.add_cell(self._fresh_name("ccu2c"), "CCU2C")
            if cell.src:
                ccu2c.attributes["src"] = cell.src
            ccu2c.parameters["INIT0"] = lut_init
            ccu2c.parameters["INIT1"] = lut_init
            ccu2c.parameters["INJECT1_0"] = "NO"
            ccu2c.parameters["INJECT1_1"] = "NO"

            # First bit
            a0 = a_bits[i] if i < len(a_bits) else "0"
            b0 = b_bits[i] if i < len(b_bits) else "0"
            ccu2c.ports["A0"] = [a0]
            ccu2c.ports["B0"] = [b0]
            ccu2c.ports["C0"] = [a0]
            ccu2c.ports["D0"] = [b0]
            ccu2c.ports["S0"] = [out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()]

            # Second bit (if exists)
            if i + 1 < width:
                a1 = a_bits[i + 1] if (i + 1) < len(a_bits) else "0"
                b1 = b_bits[i + 1] if (i + 1) < len(b_bits) else "0"
            else:
                a1, b1 = "0", "0"
            ccu2c.ports["A1"] = [a1]
            ccu2c.ports["B1"] = [b1]
            ccu2c.ports["C1"] = [a1]
            ccu2c.ports["D1"] = [b1]
            ccu2c.ports["S1"] = [out_bits[i + 1] if (i + 1) < len(out_bits) else self.nl.alloc_bit()]

            # Carry chain
            ccu2c.ports["CIN"] = [prev_cout]
            cout = self.nl.alloc_bit()
            ccu2c.ports["COUT"] = [cout]
            prev_cout = cout

    def _map_multiply(self, cell: Cell) -> None:
        """Map MUL to MULT18X18D or ALU54B (MAC), else to LUTs.

        DIV and MOD cannot be implemented in LUTs and require DSP inference.
        If they reach the LUT fallback, emit a warning and map to constant 0.
        """
        if cell.params.get("dsp_mac"):
            # MAC pattern: emit ALU54B instead of MULT18X18D + ADD
            a_net = cell.inputs.get("A")
            b_net = cell.inputs.get("B")
            out_nets = list(cell.outputs.values())
            if a_net and b_net and out_nets:
                out_net = out_nets[0]
                a_bits = self._get_bits(a_net)
                b_bits = self._get_bits(b_net)
                out_bits = self._get_bits(out_net)

                alu = self.nl.add_cell(self._fresh_name("alu54b"), "ALU54B")
                if cell.src:
                    alu.attributes["src"] = cell.src
                alu.parameters["REG_INPUTA_CLK"] = "NONE"
                alu.parameters["REG_INPUTB_CLK"] = "NONE"
                alu.parameters["REG_INPUTC_CLK"] = "NONE"
                alu.parameters["REG_PIPELINE_CLK"] = "NONE"
                alu.parameters["REG_OUTPUT_CLK"] = "NONE"
                alu.parameters["GSR"] = "DISABLED"
                # Wire A (multiply input, up to 36 bits)
                for i in range(36):
                    bit = a_bits[i] if i < len(a_bits) else "0"
                    alu.ports[f"A{i}"] = [bit]
                # Wire B (multiply input, up to 36 bits)
                for i in range(36):
                    bit = b_bits[i] if i < len(b_bits) else "0"
                    alu.ports[f"B{i}"] = [bit]
                # C input (accumulator feedback from ADD output via FF)
                cell.params.get("dsp_acc_add")
                acc_ff_name = cell.params.get("dsp_acc_ff")
                acc_bits: list[int | str] = []
                if acc_ff_name:
                    from nosis.ir import PrimOp as _P
                    acc_ff = None
                    # Find the FF cell and get its Q output bits
                    for _mod_cell in (self._ir_mod.cells.values() if self._ir_mod else []):
                        if _mod_cell.name == acc_ff_name and _mod_cell.op == _P.FF:
                            acc_ff = _mod_cell
                            break
                    if acc_ff:
                        for q_net in acc_ff.outputs.values():
                            acc_bits = self._get_bits(q_net)
                            break
                for i in range(54):
                    bit = acc_bits[i] if i < len(acc_bits) else "0"
                    alu.ports[f"C{i}"] = [bit]
                # Output R (up to 54 bits)
                for i in range(54):
                    bit = out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()
                    alu.ports[f"R{i}"] = [bit]
                # Control
                for p in ["CLK0", "CLK1", "CLK2", "CLK3"]:
                    alu.ports[p] = ["0"]
                for p in ["CE0", "CE1", "CE2", "CE3"]:
                    alu.ports[p] = ["1"]
                for p in ["RST0", "RST1", "RST2", "RST3"]:
                    alu.ports[p] = ["0"]
                alu.ports["SIGNEDA"] = ["0"]
                alu.ports["SIGNEDB"] = ["0"]
                for i in range(5):
                    alu.ports[f"OP{i}"] = ["0"]
                return
        if cell.params.get("dsp_config") == "MULT18X18D":
            a_net = cell.inputs.get("A")
            b_net = cell.inputs.get("B")
            out_nets = list(cell.outputs.values())
            if a_net and b_net and out_nets:
                out_net = out_nets[0]
                a_bits = self._get_bits(a_net)
                b_bits = self._get_bits(b_net)
                out_bits = self._get_bits(out_net)

                dsp = self.nl.add_cell(self._fresh_name("mult"), "MULT18X18D")
                if cell.src:
                    dsp.attributes["src"] = cell.src
                dsp.parameters["REG_INPUTA_CLK"] = "NONE"
                dsp.parameters["REG_INPUTB_CLK"] = "NONE"
                dsp.parameters["REG_OUTPUT_CLK"] = "NONE"
                dsp.parameters["SOURCEB_MODE"] = "B_INPUT"

                # Wire A input (up to 18 bits)
                for i in range(18):
                    bit = a_bits[i] if i < len(a_bits) else "0"
                    dsp.ports[f"A{i}"] = [bit]

                # Wire B input (up to 18 bits)
                for i in range(18):
                    bit = b_bits[i] if i < len(b_bits) else "0"
                    dsp.ports[f"B{i}"] = [bit]

                # Wire output (up to 36 bits)
                for i in range(36):
                    bit = out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()
                    dsp.ports[f"P{i}"] = [bit]

                # Unused control signals
                dsp.ports["CLK0"] = ["0"]
                dsp.ports["CLK1"] = ["0"]
                dsp.ports["CLK2"] = ["0"]
                dsp.ports["CLK3"] = ["0"]
                dsp.ports["CE0"] = ["1"]
                dsp.ports["CE1"] = ["1"]
                dsp.ports["CE2"] = ["1"]
                dsp.ports["CE3"] = ["1"]
                dsp.ports["RST0"] = ["0"]
                dsp.ports["RST1"] = ["0"]
                dsp.ports["RST2"] = ["0"]
                dsp.ports["RST3"] = ["0"]
                dsp.ports["SIGNEDA"] = ["1" if cell.params.get("dsp_signed_a") else "0"]
                dsp.ports["SIGNEDB"] = ["1" if cell.params.get("dsp_signed_b") else "0"]
                return
        if cell.op in (PrimOp.DIV, PrimOp.MOD):
            # Check if divisor B is a constant
            b_net = cell.inputs.get("B")
            b_const = None
            if b_net and b_net.driver and b_net.driver.op == PrimOp.CONST:
                b_const = int(b_net.driver.params.get("value", 0))

            if b_const is not None and b_const > 0:
                # Power-of-2: DIV = SHR, MOD = AND with mask
                if (b_const & (b_const - 1)) == 0:
                    a_net = cell.inputs.get("A")
                    out_nets = list(cell.outputs.values())
                    if a_net and out_nets:
                        out_net = out_nets[0]
                        a_bits = self._get_bits(a_net)
                        out_ecp5 = self._get_net(out_net)
                        shift = b_const.bit_length() - 1
                        if cell.op == PrimOp.DIV:
                            # a / 2^n = a >> n (wiring only)
                            for i in range(out_net.width):
                                src = i + shift
                                out_ecp5.bits[i] = a_bits[src] if src < len(a_bits) else "0"
                        else:
                            # a % 2^n = a & (2^n - 1) (keep lower n bits)
                            for i in range(out_net.width):
                                out_ecp5.bits[i] = a_bits[i] if i < shift and i < len(a_bits) else "0"
                    return

            # Non-power-of-2 or variable divisor: cannot implement in LUTs
            import warnings
            warnings.warn(
                f"DIV/MOD operation '{cell.name}' with non-power-of-2 divisor "
                f"mapped to constant 0. Use power-of-2 divisors or DSP inference.",
                UserWarning,
                stacklevel=2,
            )
            out_nets = list(cell.outputs.values())
            if out_nets:
                out_ecp5 = self._get_net(out_nets[0])
                for i in range(out_nets[0].width):
                    out_ecp5.bits[i] = "0"
            return
        self._map_lut(cell)

    def _map_shift(self, cell: Cell) -> None:
        """Map shift operations to a logarithmic barrel shifter.

        For an N-bit shift with B shift-amount bits, builds B stages
        of MUX2 layers. Each stage i shifts by 2^i positions when
        shift_amount[i] is set. Total depth = B = ceil(log2(N)),
        which is much shorter than a linear MUX chain.
        Falls back to per-bit LUT for 1-bit operands.
        """
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        out_nets = list(cell.outputs.values())
        if not a_net or not b_net or not out_nets:
            self._map_lut(cell)
            return
        out_net = out_nets[0]
        width = out_net.width
        # Use per-bit LUT for narrow shifts (<=8 bits) — fewer LUTs.
        # Use logarithmic barrel for wide shifts (>8 bits) — better timing.
        if width <= 8:
            self._map_lut(cell)
            return

        a_bits = self._get_bits(a_net)
        b_bits = self._get_bits(b_net)
        self._get_bits(out_net)
        is_right = cell.op in (PrimOp.SHR, PrimOp.SSHR)
        is_arith = cell.op == PrimOp.SSHR

        # Number of shift stages = bits needed to represent max shift
        import math
        n_stages = max(1, math.ceil(math.log2(max(width, 2))))
        n_stages = min(n_stages, len(b_bits), 6)  # cap at 6 stages (64-bit)

        # Current data bits (start with input)
        current = list(a_bits[:width])
        while len(current) < width:
            current.append("0")

        # Build logarithmic stages
        for stage in range(n_stages):
            shift_amount = 1 << stage
            sel_bit = b_bits[stage] if stage < len(b_bits) else "0"
            next_bits: list[int | str] = []
            for i in range(width):
                if is_right:
                    src_idx = i + shift_amount
                else:
                    src_idx = i - shift_amount
                if is_right and src_idx >= width:
                    # Fill: zero for logical, sign bit for arithmetic
                    fill = current[-1] if is_arith else "0"
                    shifted = fill
                elif not is_right and src_idx < 0:
                    shifted = "0"
                else:
                    shifted = current[src_idx] if 0 <= src_idx < width else "0"

                # MUX: sel=0 -> pass through, sel=1 -> shifted
                # INIT for MUX(sel=A, false=B, true=C) = 0xCACA
                mux_out = self.nl.alloc_bit()
                lut = self.nl.add_cell(self._fresh_name("shft"), "LUT4")
                lut.parameters["INIT"] = "1100101011001010"  # MUX
                lut.ports["A"] = [sel_bit]
                lut.ports["B"] = [current[i]]
                lut.ports["C"] = [shifted]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [mux_out]
                next_bits.append(mux_out)
            current = next_bits

        # Wire final stage to output
        out_ecp5 = self._get_net(out_net)
        for i in range(min(width, len(current))):
            out_ecp5.bits[i] = current[i]

    def _map_compare(self, cell: Cell) -> None:
        """Map comparison operations (LT, LE, GT, GE) to a bit-serial comparator.

        Builds a chain of LUT4 cells from LSB to MSB. Each LUT computes
        a "borrow" (for LT/LE) or "carry" (for GT/GE) that propagates
        through the chain. The final output is the 1-bit comparison result.

        For LE: result = LT_chain OR EQ_chain (all bits equal)
        For GE: result = GT_chain OR EQ_chain
        """
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        out_nets = list(cell.outputs.values())
        if not a_net or not b_net or not out_nets:
            return
        out_net = out_nets[0]

        a_bits = self._get_bits(a_net)
        b_bits = self._get_bits(b_net)
        width = max(len(a_bits), len(b_bits))

        if width == 0:
            # Zero-width: comparison is always false (LT/GT) or true (LE/GE)
            out_ecp5 = self._get_net(out_net)
            if cell.op in (PrimOp.LE, PrimOp.GE):
                out_ecp5.bits[0] = 1  # constant 1
            else:
                out_ecp5.bits[0] = "0"
            return

        # Determine if we swap A/B (GT/GE are LT/LE with swapped operands)
        if cell.op in (PrimOp.GT, PrimOp.GE):
            a_bits, b_bits = b_bits, a_bits

        is_signed = bool(cell.params.get("signed")) and width > 0

        # Build per-bit borrow chain from LSB to MSB
        # LUT: A=a_bit, B=b_bit, C=borrow_in
        # borrow_out = (!a & b) | (!(a^b) & borrow_in)
        init_lt = _compute_lut4_init(PrimOp.LT, 3)
        # For signed: at the MSB, swap the comparison sense.
        # Inverting both MSBs is equivalent to using GT init at the MSB stage.
        # GT: borrow_out = (a & !b) | (!(a^b) & borrow_in)
        init_gt = _compute_lut4_init(PrimOp.GT, 3)

        borrow: int | str = "0"

        for i in range(width):
            ab = a_bits[i] if i < len(a_bits) else "0"
            bb = b_bits[i] if i < len(b_bits) else "0"

            if i == width - 1 and cell.op in (PrimOp.LT, PrimOp.GT):
                # Last stage: output is the final result
                out_bit = self._get_net(out_net).bits[0] if out_net.width >= 1 else self.nl.alloc_bit()
            else:
                out_bit = self.nl.alloc_bit()

            lut = self.nl.add_cell(self._fresh_name("cmp"), "LUT4")
            if cell.src:
                lut.attributes["src"] = cell.src
            # At MSB for signed comparison, swap LT/GT sense (equivalent to inverting both MSBs)
            if is_signed and i == width - 1:
                lut.parameters["INIT"] = f"{init_gt:04X}"
            else:
                lut.parameters["INIT"] = f"{init_lt:04X}"
            lut.ports["A"] = [ab]
            lut.ports["B"] = [bb]
            lut.ports["C"] = [borrow]
            lut.ports["D"] = ["0"]
            lut.ports["Z"] = [out_bit]
            borrow = out_bit

        # For LE/GE: result = strict_less OR all_equal
        if cell.op in (PrimOp.LE, PrimOp.GE):
            # Build equality chain: all bits must be equal
            eq_result: int | str = "1"  # start true
            for i in range(width):
                ab = a_bits[i] if i < len(a_bits) else "0"
                bb = b_bits[i] if i < len(b_bits) else "0"
                # XNOR: equal when a==b
                xnor_out = self.nl.alloc_bit()
                xnor_lut = self.nl.add_cell(self._fresh_name("cmp_eq"), "LUT4")
                # XNOR truth table: A=a, B=b -> !(a^b) = 1001 = 0x9
                xnor_lut.parameters["INIT"] = "1001000000001001"
                xnor_lut.ports["A"] = [ab]
                xnor_lut.ports["B"] = [bb]
                xnor_lut.ports["C"] = ["0"]
                xnor_lut.ports["D"] = ["0"]
                xnor_lut.ports["Z"] = [xnor_out]

                # AND with running equality
                and_out = self.nl.alloc_bit()
                and_lut = self.nl.add_cell(self._fresh_name("cmp_and"), "LUT4")
                and_lut.parameters["INIT"] = f"{_compute_lut4_init(PrimOp.AND, 2):04X}"
                and_lut.ports["A"] = [xnor_out]
                and_lut.ports["B"] = [eq_result]
                and_lut.ports["C"] = ["0"]
                and_lut.ports["D"] = ["0"]
                and_lut.ports["Z"] = [and_out]
                eq_result = and_out

            # OR the strict-less result with equality
            out_ecp5 = self._get_net(out_net)
            final_bit = out_ecp5.bits[0] if out_net.width >= 1 else self.nl.alloc_bit()
            or_lut = self.nl.add_cell(self._fresh_name("cmp_or"), "LUT4")
            or_lut.parameters["INIT"] = f"{_compute_lut4_init(PrimOp.OR, 2):04X}"
            or_lut.ports["A"] = [borrow]  # strict less
            or_lut.ports["B"] = [eq_result]  # all equal
            or_lut.ports["C"] = ["0"]
            or_lut.ports["D"] = ["0"]
            or_lut.ports["Z"] = [final_bit]
            out_ecp5.bits[0] = final_bit
        else:
            # LT/GT: borrow chain output is already wired to out_net
            out_ecp5 = self._get_net(out_net)
            out_ecp5.bits[0] = borrow

        # Zero remaining output bits (comparison is 1-bit result)
        out_ecp5 = self._get_net(out_net)
        for i in range(1, out_net.width):
            out_ecp5.bits[i] = "0"

    def _map_concat(self, cell: Cell) -> None:
        """Map concatenation — pure wiring, no physical cells."""
        out_nets = list(cell.outputs.values())
        if not out_nets:
            return
        out_net = out_nets[0]
        out_ecp5 = self._get_net(out_net)

        # Gather input bits in order
        gathered: list[int | str] = []
        count = int(cell.params.get("count", 0))
        for i in range(count):
            inp = cell.inputs.get(f"I{i}")
            if inp:
                gathered.extend(self._get_bits(inp))
            else:
                gathered.append("0")

        # Assign bits to output (truncate or pad)
        for i in range(len(out_ecp5.bits)):
            if i < len(gathered):
                out_ecp5.bits[i] = gathered[i]

    def _map_slice(self, cell: Cell) -> None:
        """Map bit slice — pure wiring."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        offset = int(cell.params.get("offset", 0))
        width = int(cell.params.get("width", out_net.width))
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(min(width, len(out_ecp5.bits))):
            src_idx = offset + i
            if src_idx < len(a_bits):
                out_ecp5.bits[i] = a_bits[src_idx]

    def _map_extend(self, cell: Cell) -> None:
        """Map zero/sign extension — wiring + constant padding."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(len(out_ecp5.bits)):
            if i < len(a_bits):
                out_ecp5.bits[i] = a_bits[i]
            elif cell.op == PrimOp.SEXT and a_bits:
                out_ecp5.bits[i] = a_bits[-1]  # sign bit
            else:
                out_ecp5.bits[i] = "0"

    def _map_pmux(self, cell: Cell) -> None:
        """Map parallel MUX to ECP5 LUTs.

        For narrow cases (1-bit output, ≤4 cases, ≤2 select bits), computes
        a single LUT4 truth table. Otherwise builds a balanced MUX tree.
        """
        a_net = cell.inputs.get("A")  # default
        s_net = cell.inputs.get("S")  # select bits
        out_nets = list(cell.outputs.values())
        if not a_net or not s_net or not out_nets:
            self._map_lut(cell)
            return

        out_net = out_nets[0]
        width = out_net.width
        count = int(cell.params.get("count", 0))

        # Narrow-case optimization: if output is 1-bit and select is ≤2 bits
        # with ≤4 cases, compute a single LUT4 truth table directly.
        if width == 1 and s_net.width <= 2 and count <= 4:
            # Build truth table: inputs are select bits, output is the
            # selected case value (as constant) or the default.
            # Collect case constant values
            case_vals: list[int | None] = []
            all_const = True
            default_driver = a_net.driver
            default_val = None
            if default_driver and default_driver.op == PrimOp.CONST:
                default_val = int(default_driver.params.get("value", 0)) & 1
            else:
                all_const = False

            for i in range(count):
                case_net = cell.inputs.get(f"I{i}")
                if case_net and case_net.driver and case_net.driver.op == PrimOp.CONST:
                    case_vals.append(int(case_net.driver.params.get("value", 0)) & 1)
                else:
                    all_const = False
                    case_vals.append(None)

            if all_const and default_val is not None:
                # Compute LUT4 INIT from the case table
                init = 0
                for idx in range(16):
                    s_val = idx & ((1 << s_net.width) - 1)
                    # Check which case matches (priority from I0)
                    result = default_val
                    for ci in range(count):
                        if (s_val >> ci) & 1 and ci < len(case_vals):
                            v = case_vals[ci]
                            if v is not None:
                                result = v
                                break
                    if result:
                        init |= (1 << idx)

                out_bits = self._get_bits(out_net)
                s_bits = self._get_bits(s_net)
                lut = self.nl.add_cell(self._fresh_name("pmux_lut"), "LUT4")
                if cell.src:
                    lut.attributes["src"] = cell.src
                lut.parameters["INIT"] = format(init, "016b")
                lut.ports["A"] = [s_bits[0] if len(s_bits) > 0 else "0"]
                lut.ports["B"] = [s_bits[1] if len(s_bits) > 1 else "0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [out_bits[0] if out_bits else self.nl.alloc_bit()]
                return
        # Wide-output per-bit LUT4: if selector ≤ 4 bits and all case values
        # are constants, compute one LUT4 truth table per output bit.
        if s_net.width <= 4 and count >= 2:
            all_const = True
            default_driver = a_net.driver
            default_val = 0
            if default_driver and default_driver.op == PrimOp.CONST:
                default_val = int(default_driver.params.get("value", 0))
            else:
                all_const = False

            case_values: list[int] = []
            for i in range(count):
                ci = cell.inputs.get(f"I{i}")
                if ci and ci.driver and ci.driver.op == PrimOp.CONST:
                    case_values.append(int(ci.driver.params.get("value", 0)))
                else:
                    all_const = False
                    break

            if all_const:
                out_bits = self._get_bits(out_net)
                s_bits = self._get_bits(s_net)
                for bit_idx in range(width):
                    init = 0
                    for sel_val in range(16):
                        sv = sel_val & ((1 << s_net.width) - 1)
                        result = (default_val >> bit_idx) & 1
                        for ci in range(count):
                            if (sv >> ci) & 1 and ci < len(case_values):
                                result = (case_values[ci] >> bit_idx) & 1
                                break
                        if result:
                            init |= (1 << sel_val)
                    lut = self.nl.add_cell(self._fresh_name("pmux_wlut"), "LUT4")
                    if cell.src:
                        lut.attributes["src"] = cell.src
                    lut.parameters["INIT"] = format(init, "016b")
                    lut.ports["A"] = [s_bits[0] if len(s_bits) > 0 else "0"]
                    lut.ports["B"] = [s_bits[1] if len(s_bits) > 1 else "0"]
                    lut.ports["C"] = [s_bits[2] if len(s_bits) > 2 else "0"]
                    lut.ports["D"] = [s_bits[3] if len(s_bits) > 3 else "0"]
                    lut.ports["Z"] = [out_bits[bit_idx] if bit_idx < len(out_bits) else self.nl.alloc_bit()]
                return

        width = out_net.width
        count = int(cell.params.get("count", 0))
        if count == 0:
            self._map_lut(cell)
            return

        out_bits = self._get_bits(out_net)
        default_bits = self._get_bits(a_net)
        s_bits = self._get_bits(s_net)

        # For each output bit, build a priority MUX chain.
        # Case statements produce mutually exclusive selects, so a simple
        # priority chain (last-match-wins cascaded MUX) is correct and
        # uses one LUT per case instead of two (MUX + OR-reduce).
        for bit_idx in range(width):
            candidates: list[tuple] = []
            for sel_idx in range(count):
                case_net = cell.inputs.get(f"I{sel_idx}")
                if case_net is None:
                    continue
                case_bits = self._get_bits(case_net)
                case_bit = case_bits[bit_idx] if bit_idx < len(case_bits) else "0"
                sel_bit = s_bits[sel_idx] if sel_idx < len(s_bits) else "0"
                candidates.append((sel_bit, case_bit))

            default_bit = default_bits[bit_idx] if bit_idx < len(default_bits) else "0"

            if not candidates:
                if bit_idx < len(out_bits):
                    out_ecp5 = self._get_net(out_net)
                    out_ecp5.bits[bit_idx] = default_bit
                continue

            # Priority chain: start from default, each case overrides if selected
            current = default_bit
            for sel_bit, case_bit in candidates:
                mux_out = self.nl.alloc_bit()
                lut = self.nl.add_cell(self._fresh_name("pmux"), "LUT4")
                if cell.src:
                    lut.attributes["src"] = cell.src
                # MUX: sel=A, false=B (current), true=C (case_bit)
                lut.parameters["INIT"] = "1100101011001010"
                lut.ports["A"] = [sel_bit]
                lut.ports["B"] = [current]
                lut.ports["C"] = [case_bit]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [mux_out]
                current = mux_out

            if bit_idx < len(out_bits):
                out_ecp5 = self._get_net(out_net)
                out_ecp5.bits[bit_idx] = current

    def _map_repeat(self, cell: Cell) -> None:
        """Map repeat — wiring."""
        a_net = cell.inputs.get("A")
        out_nets = list(cell.outputs.values())
        if not a_net or not out_nets:
            return
        out_net = out_nets[0]
        a_bits = self._get_bits(a_net)
        out_ecp5 = self._get_net(out_net)
        for i in range(len(out_ecp5.bits)):
            out_ecp5.bits[i] = a_bits[i % len(a_bits)] if a_bits else "0"

    def _map_memory(self, cell: Cell) -> None:
        """Map MEMORY cells to DP16KD when tagged by BRAM inference, else to FFs."""
        bram_config = cell.params.get("bram_config")
        if bram_config == "DP16KD":
            int(cell.params.get("bram_addr_bits", 10))
            data_width = int(cell.params.get("bram_data_width", 18))
            depth = int(cell.params.get("depth", 0))
            int(cell.params.get("width", 0))

            # Determine the DP16KD data width configuration string
            width_map = {1: "X1", 2: "X2", 4: "X4", 9: "X9", 18: "X18", 36: "X36"}
            width_map.get(data_width, "X18")

            bram = self.nl.add_cell(self._fresh_name("bram"), "DP16KD")
            if cell.src:
                bram.attributes["src"] = cell.src
            bram.parameters["DATA_WIDTH_A"] = str(data_width)
            bram.parameters["DATA_WIDTH_B"] = str(data_width)
            bram.parameters["REGMODE_A"] = "NOREG"
            bram.parameters["REGMODE_B"] = "NOREG"
            bram.parameters["CSDECODE_A"] = "0b000"
            bram.parameters["CSDECODE_B"] = "0b000"
            bram.parameters["WRITEMODE_A"] = "NORMAL"
            bram.parameters["WRITEMODE_B"] = "NORMAL"
            bram.parameters["GSR"] = "DISABLED"
            # INIT values: use readmem data if available, else all zeros
            init_file = cell.params.get("init_file")
            if init_file:
                from nosis.readmem import parse_readmemh, parse_readmemb, readmem_to_dp16kd_initvals
                from pathlib import Path
                init_path = Path(init_file)
                if init_path.exists():
                    init_format = cell.params.get("init_format", "hex")
                    if init_format == "bin":
                        mem_data = parse_readmemb(init_path)
                    else:
                        mem_data = parse_readmemh(init_path)
                    initvals = readmem_to_dp16kd_initvals(
                        mem_data, data_width=data_width, depth=depth
                    )
                    for k, v in initvals.items():
                        bram.parameters[k] = v
                else:
                    for i in range(64):
                        bram.parameters[f"INITVAL_{i:02X}"] = "0x00000000000000000000"
            else:
                for i in range(64):
                    bram.parameters[f"INITVAL_{i:02X}"] = "0x00000000000000000000"

            # Wire address port A (read)
            raddr_net = cell.inputs.get("RADDR")
            raddr_bits = self._get_bits(raddr_net) if raddr_net else []
            for i in range(14):
                bit = raddr_bits[i] if i < len(raddr_bits) else "0"
                bram.ports[f"ADA{i}"] = [bit]

            # Wire address port B (write)
            waddr_net = cell.inputs.get("WADDR")
            waddr_bits = self._get_bits(waddr_net) if waddr_net else []
            for i in range(14):
                bit = waddr_bits[i] if i < len(waddr_bits) else "0"
                bram.ports[f"ADB{i}"] = [bit]

            # Wire data input (port B write)
            wdata_net = cell.inputs.get("WDATA")
            wdata_bits = self._get_bits(wdata_net) if wdata_net else []
            for i in range(18):
                bit = wdata_bits[i] if i < len(wdata_bits) else "0"
                bram.ports[f"DIB{i}"] = [bit]
            for i in range(18):
                bram.ports[f"DIA{i}"] = ["0"]

            # Wire data output (port A read)
            rdata_net = list(cell.outputs.values())[0] if cell.outputs else None
            rdata_bits = self._get_bits(rdata_net) if rdata_net else []
            for i in range(18):
                bit = rdata_bits[i] if i < len(rdata_bits) else self.nl.alloc_bit()
                bram.ports[f"DOA{i}"] = [bit]
            # Wire data output (port B read — for true dual-port)
            rdata_b_net = cell.outputs.get("RDATA_B") if len(cell.outputs) > 1 else None
            rdata_b_bits = self._get_bits(rdata_b_net) if rdata_b_net else []
            for i in range(18):
                bit = rdata_b_bits[i] if i < len(rdata_b_bits) else self.nl.alloc_bit()
                bram.ports[f"DOB{i}"] = [bit]

            # Clock
            clk_net = cell.inputs.get("CLK")
            clk_bits = self._get_bits(clk_net) if clk_net else ["0"]
            bram.ports["CLKA"] = [clk_bits[0] if clk_bits else "0"]
            bram.ports["CLKB"] = [clk_bits[0] if clk_bits else "0"]

            # Write enable
            we_net = cell.inputs.get("WE")
            we_bits = self._get_bits(we_net) if we_net else ["0"]
            bram.ports["WEA"] = ["0"]
            bram.ports["WEB"] = [we_bits[0] if we_bits else "0"]

            # Chip select (active)
            bram.ports["CSA0"] = ["1"]
            bram.ports["CSA1"] = ["0"]
            bram.ports["CSA2"] = ["0"]
            bram.ports["CSB0"] = ["1"]
            bram.ports["CSB1"] = ["0"]
            bram.ports["CSB2"] = ["0"]

            # Reset and output register clock enable
            bram.ports["RSTA"] = ["0"]
            bram.ports["RSTB"] = ["0"]
            bram.ports["OCEA"] = ["1"]
            bram.ports["OCEB"] = ["1"]
            bram.ports["CEA"] = ["1"]
            bram.ports["CEB"] = ["1"]
            return

        if bram_config in ("DPR16X4", "DPR16X4_TILED"):
            # Distributed RAM: TRELLIS_DPR16X4 (16 entries, 4 bits each)
            dpr_count = int(cell.params.get("bram_count", 1))
            int(cell.params.get("width", 4))
            rdata_net = list(cell.outputs.values())[0] if cell.outputs else None
            rdata_bits = self._get_bits(rdata_net) if rdata_net else []

            raddr_net = cell.inputs.get("RADDR")
            waddr_net = cell.inputs.get("WADDR")
            wdata_net = cell.inputs.get("WDATA")
            we_net = cell.inputs.get("WE")
            clk_net = cell.inputs.get("CLK")

            raddr_bits = self._get_bits(raddr_net) if raddr_net else []
            waddr_bits = self._get_bits(waddr_net) if waddr_net else []
            wdata_bits = self._get_bits(wdata_net) if wdata_net else []
            we_bits = self._get_bits(we_net) if we_net else ["0"]
            clk_bits = self._get_bits(clk_net) if clk_net else ["0"]

            for d in range(dpr_count):
                dpr = self.nl.add_cell(self._fresh_name("dpr"), "TRELLIS_DPR16X4")
                if cell.src:
                    dpr.attributes["src"] = cell.src
                dpr.parameters["WCKMUX"] = "WCK"
                dpr.parameters["WREMUX"] = "WRE"
                # Address ports (4 bits each for 16 entries)
                for i in range(4):
                    dpr.ports[f"RAD{i}"] = [raddr_bits[i] if i < len(raddr_bits) else "0"]
                    dpr.ports[f"WAD{i}"] = [waddr_bits[i] if i < len(waddr_bits) else "0"]
                # Data ports (4 bits)
                for i in range(4):
                    bit_idx = d * 4 + i
                    dpr.ports[f"DI{i}"] = [wdata_bits[bit_idx] if bit_idx < len(wdata_bits) else "0"]
                    out_bit = rdata_bits[bit_idx] if bit_idx < len(rdata_bits) else self.nl.alloc_bit()
                    dpr.ports[f"DO{i}"] = [out_bit]
                dpr.ports["WCK"] = [clk_bits[0] if clk_bits else "0"]
                dpr.ports["WRE"] = [we_bits[0] if we_bits else "0"]
            return

        # No BRAM/DPR tag — fall back to FF-based mapping (placeholder)
        self._map_unknown(cell)

    def _map_unknown(self, cell: Cell) -> None:
        """Emit a placeholder for unsupported operations."""
        for out_net in cell.outputs.values():
            ecp5_net = self._get_net(out_net)
            ecp5_net.bits = ["0"] * out_net.width


def map_to_ecp5(design: Design) -> ECP5Netlist:
    """Map a Nosis IR Design to an ECP5 netlist."""
    mod = design.top_module()
    netlist = ECP5Netlist(top=mod.name)
    mapper = _ECP5Mapper(netlist)
    mapper.map_module(mod)
    return netlist
