"""ECP5 technology mapper — converts IR to ECP5 netlist cells."""

from __future__ import annotations

from nosis.ir import Cell, Design, Module, Net, PrimOp
from nosis.techmap.netlist import ECP5Net, ECP5Netlist, _compute_lut4_init, _const_bits

class _ECP5Mapper:
    """Maps a Nosis IR Module to an ECP5Netlist."""

    def __init__(self, netlist: ECP5Netlist) -> None:
        self.nl = netlist
        self._cell_counter = 0
        self._bit_alias: dict[int, int | str] = {}
        self._net_map: dict[str, ECP5Net] = {}
        self._ir_mod: Module | None = None

    def _set_bit(self, bits: list, idx: int, new_val: int | str) -> None:
        """Set bits[idx] = new_val, recording alias if it replaces an allocated bit."""
        old = bits[idx]
        bits[idx] = new_val
        if isinstance(old, int) and old >= 2 and old != new_val:
            self._bit_alias[old] = new_val

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

    @staticmethod
    def _topo_sort(mod: Module) -> list:
        """Sort IR cells so producers come before consumers."""
        from nosis.ir import PrimOp
        net_driver: dict[str, str] = {}
        for cell in mod.cells.values():
            for net in cell.outputs.values():
                net_driver[net.name] = cell.name

        deps: dict[str, set[str]] = {c.name: set() for c in mod.cells.values()}
        for cell in mod.cells.values():
            for net in cell.inputs.values():
                drv = net_driver.get(net.name)
                if drv and drv != cell.name:
                    deps[cell.name].add(drv)

        order = []
        visited: set[str] = set()
        temp: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in temp:
                return  # cycle — break it
            temp.add(name)
            for dep in sorted(deps.get(name, ())):
                visit(dep)
            temp.discard(name)
            visited.add(name)
            order.append(name)

        for name in sorted(mod.cells):
            visit(name)

        return [mod.cells[n] for n in order if n in mod.cells]

    def map_module(self, mod: Module) -> None:
        """Map all cells in an IR module to ECP5 cells."""
        self._ir_mod = mod
        # First pass: create ECP5 nets for all IR nets (sorted for determinism)
        for net in sorted(mod.nets.values(), key=lambda n: n.name):
            self._get_net(net)

        # Map ports (sorted for determinism)
        for port_name, port_net in sorted(mod.ports.items()):
            ecp5_net = self._get_net(port_net)
            # Determine direction from the IR cells
            direction = "input"
            actual_net = ecp5_net  # default: use port net
            for cell in mod.cells.values():
                if cell.op == PrimOp.OUTPUT and cell.params.get("port_name", "") == port_name:
                    direction = "output"
                    # Use the OUTPUT cell's INPUT net for the port bits —
                    # the Q-redirect may have changed the input to a different net.
                    for inp_net in cell.inputs.values():
                        actual_net = self._get_net(inp_net)
                    break
                if cell.op == PrimOp.OUTPUT:
                    for inp_net in cell.inputs.values():
                        if inp_net.name == port_name:
                            direction = "output"
                            actual_net = self._get_net(inp_net)
                            break
                if cell.op == PrimOp.INPUT:
                    if cell.params.get("inout"):
                        direction = "inout"
                        break

            self.nl.ports[port_name] = {
                "direction": direction,
                "bits": actual_net.bits,
            }

        # Pre-pass: detect shared reset signals for safe LSR extraction.
        # Only extract when the same MUX select drives the outermost D-MUX
        # of >= 2 FFs (true reset signals fan out; data constants don't).
        self._lsr_candidates: dict[str, tuple] = {}
        _sel_counts: dict[str, int] = {}
        _sel_entries: dict[str, list] = {}
        for cell in mod.cells.values():
            if cell.op != PrimOp.FF:
                continue
            d = cell.inputs.get("D")
            if d is None or d.driver is None or d.driver.op != PrimOp.MUX:
                continue
            mux = d.driver
            s, a, b = mux.inputs.get("S"), mux.inputs.get("A"), mux.inputs.get("B")
            if s is None or a is None or b is None:
                continue
            if b.driver is not None and b.driver.op == PrimOp.CONST and b.driver.params.get("value", -1) == 0:
                _sel_counts[s.name] = _sel_counts.get(s.name, 0) + 1
                _sel_entries.setdefault(s.name, []).append((cell.name, s, a))
        for sname, entries in _sel_entries.items():
            if _sel_counts.get(sname, 0) >= 2:
                for ff_name, sel_net, data_net in entries:
                    self._lsr_candidates[ff_name] = (sel_net, data_net)

        # Pre-pass: detect CE (clock enable) patterns.
        # After LSR extraction, the remaining outermost MUX may be
        # MUX(ce, Q, data) — hold Q when ce=0, update to data when ce=1.
        # Only extract when the same select signal drives >= 2 FFs.
        self._ce_candidates: dict[str, tuple] = {}
        _ce_sel_counts: dict[str, int] = {}
        _ce_entries: dict[str, list] = {}
        for cell in mod.cells.values():
            if cell.op != PrimOp.FF:
                continue
            if cell.name in self._lsr_candidates:
                d = self._lsr_candidates[cell.name][1]
            else:
                d = cell.inputs.get("D")
            q = list(cell.outputs.values())[0] if cell.outputs else None
            if d is None or q is None or d.driver is None or d.driver.op != PrimOp.MUX:
                continue
            mux = d.driver
            s, a, b = mux.inputs.get("S"), mux.inputs.get("A"), mux.inputs.get("B")
            if s is None or a is None or b is None:
                continue
            if a is q or a.name == q.name:
                _ce_sel_counts[s.name] = _ce_sel_counts.get(s.name, 0) + 1
                _ce_entries.setdefault(s.name, []).append((cell.name, s, b, False))
            elif b is q or b.name == q.name:
                _ce_sel_counts[s.name] = _ce_sel_counts.get(s.name, 0) + 1
                _ce_entries.setdefault(s.name, []).append((cell.name, s, a, True))
        for sname, entries in _ce_entries.items():
            if _ce_sel_counts.get(sname, 0) >= 2:
                for ff_name, sel, data, inv in entries:
                    self._ce_candidates[ff_name] = (sel, data, inv)

        # Map in priority order: CONST first (for carry chain D ports),
        # then non-CONST/non-FF (MUX chains, logic), then FF last
        # (so FF DI ports see the final MUX chain outputs).
        from nosis.ir import PrimOp as _P
        for cell in sorted(mod.cells.values(), key=lambda c: c.name):
            if cell.op == _P.CONST:
                self._map_cell(cell)
        for cell in sorted(mod.cells.values(), key=lambda c: c.name):
            if cell.op not in (_P.CONST, _P.FF):
                self._map_cell(cell)
        for cell in sorted(mod.cells.values(), key=lambda c: c.name):
            if cell.op == _P.FF:
                self._map_cell(cell)

        # Note: FF DI re-resolution moved to map_to_ecp5 (after alias pass)

    def _insert_dcca_buffers(self) -> None:
        """Insert DCCA buffers for fabric-generated clocks.

        On ECP5, clock signals must go through a DCCA (Dedicated Clock
        Connect Amplifier) to reach the global clock network.  IO pins
        are automatically promoted by nextpnr, but FF outputs used as
        clocks are not.  This pass detects FF Q outputs that drive other
        FFs' CLK inputs and inserts a DCCA buffer between them.
        """
        # Find all nets used as CLK inputs on TRELLIS_FF cells
        clk_nets: set[int | str] = set()
        for cell in self.nl.cells.values():
            if cell.cell_type == "TRELLIS_FF":
                clk_bits = cell.ports.get("CLK", [])
                for b in clk_bits:
                    if isinstance(b, int):
                        clk_nets.add(b)

        # Find which of those are driven by FF Q outputs (fabric clocks)
        # vs IO pins (which nextpnr handles automatically)
        io_driven: set[int | str] = set()
        for port_name, port_info in self.nl.ports.items():
            if port_info.get("direction") == "input":
                for b in port_info.get("bits", []):
                    io_driven.add(b)

        ff_driven_clks = set()
        for cell in self.nl.cells.values():
            if cell.cell_type == "TRELLIS_FF":
                for b in cell.ports.get("Q", []):
                    if b in clk_nets and b not in io_driven:
                        ff_driven_clks.add(b)

        # Insert DCCA for each fabric clock
        for clk_bit in ff_driven_clks:
            dcca_out = self.nl.alloc_bit()
            dcca = self.nl.add_cell(self._fresh_name("dcca"), "DCCA")
            dcca.ports["CLKI"] = [clk_bit]
            dcca.ports["CLKO"] = [dcca_out]
            dcca.ports["CE"] = ["1"]
            # Rewire all FF CLK inputs from clk_bit to dcca_out
            for cell in self.nl.cells.values():
                if cell.cell_type == "TRELLIS_FF" and cell is not dcca:
                    clk_bits = cell.ports.get("CLK", [])
                    if clk_bits and clk_bits[0] == clk_bit:
                        cell.ports["CLK"] = [dcca_out]

    def _map_cell(self, cell: Cell) -> None:
        """Map a single IR cell to one or more ECP5 cells."""
        op = cell.op

        if op == PrimOp.LATCH:
            # Map latches as TRELLIS_FF with transparent enable
            # ECP5 doesn't have dedicated latches — use FF with CE as enable
            self._map_ff(cell)
            return

        # Vendor primitives — pass through as-is
        vendor = cell.params.get("_vendor_primitive")
        if vendor:
            ecp5_cell = self.nl.add_cell(self._fresh_name(vendor.lower()), vendor)
            for port_name, net in cell.inputs.items():
                ecp5_cell.ports[port_name] = self._get_bits(net)[:1] if net.width == 1 else self._get_bits(net)
            for port_name, net in cell.outputs.items():
                ecp5_cell.ports[port_name] = self._get_bits(net)[:1] if net.width == 1 else self._get_bits(net)
            return

        if op == PrimOp.INPUT or op == PrimOp.OUTPUT:
            # Tri-state buffer inference for inout ports
            if op == PrimOp.INPUT and cell.params.get("inout"):
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
            elif op == PrimOp.INPUT:
                # Wire port bits to the INPUT cell's output net.
                port_name = cell.params.get("port_name", "")
                port_net_ir = self._ir_mod.ports.get(port_name) if self._ir_mod else None
                if port_net_ir:
                    port_ecp5 = self._get_net(port_net_ir)
                    for out_net in cell.outputs.values():
                        out_ecp5 = self._get_net(out_net)
                        if out_ecp5 is not port_ecp5:
                            for i in range(min(len(out_ecp5.bits), len(port_ecp5.bits))):
                                self._set_bit(out_ecp5.bits, i, port_ecp5.bits[i])
            return  # handled as ports

        if op == PrimOp.CONST:
            self._map_const(cell)
        elif op == PrimOp.FF:
            self._map_ff(cell)
        elif op in (PrimOp.EQ, PrimOp.NE):
            self._map_equality(cell)
        elif op in (PrimOp.AND, PrimOp.OR, PrimOp.XOR, PrimOp.NOT,
                     PrimOp.MUX,
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
            new_bits = _const_bits(value, width)
            for i in range(min(len(ecp5_net.bits), len(new_bits))):
                self._set_bit(ecp5_net.bits, i, new_bits[i])

    def _map_ff(self, cell: Cell) -> None:
        """Map an IR FF to TRELLIS_FF cells (one per bit)."""
        d_net = cell.inputs.get("D")
        clk_net = cell.inputs.get("CLK")
        rst_net = cell.inputs.get("RST")
        q_net = list(cell.outputs.values())[0] if cell.outputs else None

        if d_net is None or q_net is None:
            return

        # Extract LSR from shared-reset MUX pattern
        actual_d = d_net
        lsr_net = rst_net
        if cell.name in getattr(self, '_lsr_candidates', {}):
            sel_net, data_net = self._lsr_candidates[cell.name]
            actual_d = data_net
            lsr_net = sel_net

        # Extract CE from hold-MUX pattern
        ce_net = None
        ce_invert = False
        if cell.name in getattr(self, '_ce_candidates', {}):
            ce_sel, ce_data, ce_invert = self._ce_candidates[cell.name]
            ce_net = ce_sel
            actual_d = ce_data

        width = actual_d.width
        d_bits = self._get_bits(actual_d)
        q_bits = self._get_bits(q_net)
        clk_bits = self._get_bits(clk_net) if clk_net else ["0"]
        lsr_bits = self._get_bits(lsr_net) if lsr_net else ["0"]
        ce_bits = self._get_bits(ce_net) if ce_net else None

        # Check initial value from FF cell params (set during frontend lowering)
        init_val = int(cell.params.get("init_value", 0))

        for i in range(min(width, len(d_bits), len(q_bits))):
            # Per-bit REGSET: SET if that bit of init_value is 1
            bit_init = (init_val >> i) & 1
            ff = self.nl.add_cell(self._fresh_name("tff"), "TRELLIS_FF")
            if cell.src:
                ff.attributes["src"] = cell.src
            is_async = bool(cell.params.get("async_reset", False))
            ff.parameters["GSR"] = "DISABLED"
            ff.parameters["CEMUX"] = "INV" if ce_invert else ("CE" if ce_bits else "1 ")
            ff.parameters["CLKMUX"] = "CLK"
            ff.parameters["LSRMUX"] = "LSR"
            ff.parameters["REGSET"] = "SET" if bit_init else "RESET"
            ff.parameters["SRMODE"] = "ASYNC" if is_async else "LSR_OVER_CE"
            ff.ports["CLK"] = [clk_bits[0] if clk_bits else "0"]
            ff.ports["DI"] = [d_bits[i] if i < len(d_bits) else "0"]
            ff.ports["LSR"] = [lsr_bits[0] if lsr_bits else "0"]
            if ce_bits:
                ff.ports["CE"] = [ce_bits[0]]
            ff.ports["Q"] = [q_bits[i] if i < len(q_bits) else self.nl.alloc_bit()]

    def _map_equality(self, cell: Cell) -> None:
        """Map multi-bit EQ/NE to per-bit XNOR + AND/NAND reduction tree."""
        a_net = cell.inputs.get("A")
        b_net = cell.inputs.get("B")
        out_nets = list(cell.outputs.values())
        if not a_net or not b_net or not out_nets:
            self._map_lut(cell)
            return
        out_net = out_nets[0]
        a_bits = self._get_bits(a_net)
        b_bits = self._get_bits(b_net)
        out_bits = self._get_bits(out_net)
        width = max(a_net.width, b_net.width)

        if width <= 1:
            self._map_lut(cell)
            return

        # Stage 1: per-bit XNOR (equality per bit)
        # LUT4 XNOR: INIT = 0x9999 (A XNOR B)
        eq_bits: list[int | str] = []
        for i in range(width):
            a_bit = a_bits[i] if i < len(a_bits) else "0"
            b_bit = b_bits[i] if i < len(b_bits) else "0"
            if a_bit == b_bit:
                eq_bits.append("1")
                continue
            if isinstance(a_bit, str) and isinstance(b_bit, str):
                eq_bits.append("1" if a_bit == b_bit else "0")
                continue
            eq_out = self.nl.alloc_bit()
            lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
            lut.parameters["INIT"] = "1001100110011001"  # XNOR
            lut.ports["A"] = [a_bit]
            lut.ports["B"] = [b_bit]
            lut.ports["C"] = ["0"]
            lut.ports["D"] = ["0"]
            lut.ports["Z"] = [eq_out]
            eq_bits.append(eq_out)

        # Stage 2: AND reduction tree using LUT4 (4-input AND per level)
        # INIT for 4-input AND: only bit 15 is set = 0x8000
        current = eq_bits
        while len(current) > 1:
            next_level: list[int | str] = []
            i = 0
            while i < len(current):
                chunk = current[i:i + 4]
                if len(chunk) == 1:
                    next_level.append(chunk[0])
                else:
                    out = self.nl.alloc_bit()
                    lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                    if len(chunk) == 2:
                        lut.parameters["INIT"] = "1000100010001000"  # A AND B
                        lut.ports["A"] = [chunk[0]]
                        lut.ports["B"] = [chunk[1]]
                        lut.ports["C"] = ["0"]
                        lut.ports["D"] = ["0"]
                    elif len(chunk) == 3:
                        lut.parameters["INIT"] = "1000000010000000"  # A AND B AND C
                        lut.ports["A"] = [chunk[0]]
                        lut.ports["B"] = [chunk[1]]
                        lut.ports["C"] = [chunk[2]]
                        lut.ports["D"] = ["0"]
                    else:
                        lut.parameters["INIT"] = "1000000000000000"  # A AND B AND C AND D
                        lut.ports["A"] = [chunk[0]]
                        lut.ports["B"] = [chunk[1]]
                        lut.ports["C"] = [chunk[2]]
                        lut.ports["D"] = [chunk[3]]
                    lut.ports["Z"] = [out]
                    next_level.append(out)
                i += 4
            current = next_level

        # Final: for NE, invert the result
        if cell.op == PrimOp.NE:
            inv_out = self.nl.alloc_bit()
            lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
            lut.parameters["INIT"] = "0101010101010101"  # NOT A
            lut.ports["A"] = [current[0]]
            lut.ports["B"] = ["0"]
            lut.ports["C"] = ["0"]
            lut.ports["D"] = ["0"]
            lut.ports["Z"] = [inv_out]
            current = [inv_out]

        # Wire result to output bit
        if out_bits and current:
            self._set_bit(out_bits, 0, current[0])
            ecp5_out = self._get_net(out_net)
            if ecp5_out.bits:
                self._set_bit(ecp5_out.bits, 0, current[0])

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

        # ECP5 CCU2C carry chain for ADD/SUB.
        #
        # Convention matching yosys/nextpnr:
        #   A=0, B=operand_a, C=0, D=1, INIT=0x96AA (ADD) or 0x6996 (SUB)
        #   S = LUT4(A,B,C,D) XOR CIN
        #   COUT = carry propagate
        #
        # With A=0, C=0, D=1, the LUT reduces to a function of B alone:
        #   ADD INIT 0x96AA at D=1,C=0: f(B=0)=0, f(B=1)=1 → passthrough
        #   SUB INIT 0x6996 at D=1,C=0: f(B=0)=1, f(B=1)=0 → invert
        #
        # The second operand (constant or variable) enters through CIN
        # or is folded into INIT.  For variable+variable, both go on B
        # ports of alternating cells — but the simple case (counter+1)
        # uses the yosys convention exactly.
        is_sub = (cell.op == PrimOp.SUB)

        # Standard INIT for ADD/SUB: 0x96AA.
        # SUB a-b is computed as a + ~b + 1 by the carry chain with CIN=1
        # and D="1" (the LUT inverts B when D=1).
        std_init = 0x96AA
        lut_init = format(std_init, "016b")

        packed_init = cell.params.get("packed_lut_init")
        if packed_init is not None:
            lut_init = format(int(packed_init) & 0xFFFF, "016b")

        # For ADD with a constant operand, inject the constant's bit 0
        # as the initial carry-in.  The LUT computes A XOR B with the
        # constant folded into D='1', and the constant's value enters
        # through the carry chain.
        b_const = all(isinstance(b, str) and b in ("0", "1") for b in b_bits)
        if is_sub:
            prev_cout: int | str = "1"
        elif b_const and len(b_bits) > 0 and b_bits[0] == "1":
            prev_cout: int | str = "1"  # +1: inject carry
        else:
            prev_cout: int | str = "0"

        for i in range(0, width, 2):
            ccu2c = self.nl.add_cell(self._fresh_name("ccu2c"), "CCU2C")
            if cell.src:
                ccu2c.attributes["src"] = cell.src
            ccu2c.parameters["INIT0"] = lut_init
            ccu2c.parameters["INIT1"] = lut_init
            ccu2c.parameters["INJECT1_0"] = "NO"
            ccu2c.parameters["INJECT1_1"] = "NO"

            # First bit: A=0, B=a_bit, C=0, D=b_bit (or const 1)
            a0 = a_bits[i] if i < len(a_bits) else "0"
            b0 = b_bits[i] if i < len(b_bits) else "0"
            ccu2c.ports["A0"] = ["0"]
            ccu2c.ports["B0"] = [a0]
            ccu2c.ports["C0"] = ["0"]
            ccu2c.ports["D0"] = [b0 if not isinstance(b0, str) or b0 not in ("0",) else "1"]
            ccu2c.ports["S0"] = [out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()]

            # Second bit
            if i + 1 < width:
                a1 = a_bits[i + 1] if (i + 1) < len(a_bits) else "0"
                b1 = b_bits[i + 1] if (i + 1) < len(b_bits) else "0"
            else:
                a1, b1 = "0", "0"
            ccu2c.ports["A1"] = ["0"]
            ccu2c.ports["B1"] = [a1]
            ccu2c.ports["C1"] = ["0"]
            ccu2c.ports["D1"] = [b1 if not isinstance(b1, str) or b1 not in ("0",) else "1"]
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
                alu.parameters["RESETMODE"] = "SYNC"
                alu.parameters["CLK0_DIV"] = "ENABLED"
                alu.parameters["CLK1_DIV"] = "ENABLED"
                alu.parameters["CLK2_DIV"] = "ENABLED"
                alu.parameters["CLK3_DIV"] = "ENABLED"
                alu.parameters["HIGHSPEED_CLK"] = "NONE"
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
                # All parameters required by nextpnr's ECP5 DSP packer
                dsp.parameters["REG_INPUTA_CLK"] = "NONE"
                dsp.parameters["REG_INPUTB_CLK"] = "NONE"
                dsp.parameters["REG_OUTPUT_CLK"] = "NONE"
                dsp.parameters["REG_PIPELINE_CLK"] = "NONE"
                dsp.parameters["SOURCEB_MODE"] = "B_INPUT"
                dsp.parameters["MULT_BYPASS"] = "DISABLED"
                dsp.parameters["GSR"] = "DISABLED"
                dsp.parameters["RESETMODE"] = "SYNC"
                dsp.parameters["CLK0_DIV"] = "ENABLED"
                dsp.parameters["CLK1_DIV"] = "ENABLED"
                dsp.parameters["CLK2_DIV"] = "ENABLED"
                dsp.parameters["CLK3_DIV"] = "ENABLED"
                dsp.parameters["HIGHSPEED_CLK"] = "NONE"

                # Wire A input (up to 18 bits)
                for i in range(18):
                    bit = a_bits[i] if i < len(a_bits) else "0"
                    dsp.ports[f"A{i}"] = [bit]

                # Wire B input (up to 18 bits)
                for i in range(18):
                    bit = b_bits[i] if i < len(b_bits) else "0"
                    dsp.ports[f"B{i}"] = [bit]

                # Wire C input (unused, tie to 0)
                for i in range(18):
                    dsp.ports[f"C{i}"] = ["0"]

                # Wire output (up to 36 bits)
                for i in range(36):
                    bit = out_bits[i] if i < len(out_bits) else self.nl.alloc_bit()
                    dsp.ports[f"P{i}"] = [bit]

                # Control signals
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
                dsp.ports["SOURCEA"] = ["0"]
                dsp.ports["SOURCEB"] = ["0"]
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
                                self._set_bit(out_ecp5.bits, i, a_bits[src] if src < len(a_bits) else "0")
                        else:
                            # a % 2^n = a & (2^n - 1) (keep lower n bits)
                            for i in range(out_net.width):
                                self._set_bit(out_ecp5.bits, i, a_bits[i] if i < shift and i < len(a_bits) else "0")
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
                    self._set_bit(out_ecp5.bits, i, "0")
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
                lut.parameters["INIT"] = "1110010011100100"  # MUX
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
            self._set_bit(out_ecp5.bits, i, current[i])

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
                self._set_bit(out_ecp5.bits, 0, 1)
            else:
                self._set_bit(out_ecp5.bits, 0, "0")
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
                lut.parameters["INIT"] = f"0x{init_gt:04X}"
            else:
                lut.parameters["INIT"] = f"0x{init_lt:04X}"
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
                and_lut.parameters["INIT"] = f"0x{_compute_lut4_init(PrimOp.AND, 2):04X}"
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
            or_lut.parameters["INIT"] = f"0x{_compute_lut4_init(PrimOp.OR, 2):04X}"
            or_lut.ports["A"] = [borrow]  # strict less
            or_lut.ports["B"] = [eq_result]  # all equal
            or_lut.ports["C"] = ["0"]
            or_lut.ports["D"] = ["0"]
            or_lut.ports["Z"] = [final_bit]
            self._set_bit(out_ecp5.bits, 0, final_bit)
        else:
            # LT/GT: borrow chain output is already wired to out_net
            out_ecp5 = self._get_net(out_net)
            self._set_bit(out_ecp5.bits, 0, borrow)

        # Zero remaining output bits (comparison is 1-bit result)
        out_ecp5 = self._get_net(out_net)
        for i in range(1, out_net.width):
            self._set_bit(out_ecp5.bits, i, "0")

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

        for i in range(len(out_ecp5.bits)):
            if i < len(gathered):
                self._set_bit(out_ecp5.bits, i, gathered[i])

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
                self._set_bit(out_ecp5.bits, i, a_bits[src_idx])
            else:
                self._set_bit(out_ecp5.bits, i, "0")

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
                self._set_bit(out_ecp5.bits, i, a_bits[i])
            elif cell.op == PrimOp.SEXT and a_bits:
                self._set_bit(out_ecp5.bits, i, a_bits[-1])
            else:
                self._set_bit(out_ecp5.bits, i, "0")

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
        if False and width == 1 and s_net.width <= 2 and count <= 4:
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
                is_onehot = (s_net.width == count)
                init = 0
                for idx in range(16):
                    s_val = idx & ((1 << s_net.width) - 1)
                    result = default_val
                    if is_onehot:
                        for ci in range(count):
                            if (s_val >> ci) & 1 and ci < len(case_vals):
                                v = case_vals[ci]
                                if v is not None:
                                    result = v
                                    break
                    else:
                        ci = s_val - 1
                        if 0 <= ci < len(case_vals) and case_vals[ci] is not None:
                            result = case_vals[ci]
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
        # Wide-output per-bit LUT4: DISABLED — the PMUX case-to-index
        # mapping varies between element select and case statement PMUXes.
        # Use the fallback priority chain instead.
        if False and s_net.width <= 4 and count >= 2:
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
                # Detect encoding: one-hot if S width == count, binary otherwise
                is_onehot = (s_net.width == count)
                for bit_idx in range(width):
                    init = 0
                    for sel_val in range(16):
                        sv = sel_val & ((1 << s_net.width) - 1)
                        result = (default_val >> bit_idx) & 1
                        if is_onehot:
                            for ci in range(count):
                                if (sv >> ci) & 1 and ci < len(case_values):
                                    result = (case_values[ci] >> bit_idx) & 1
                                    break
                        else:
                            # Binary select: case I_i corresponds to select value (i+1)
                            # (default A covers select value 0)
                            ci = sv - 1
                            if 0 <= ci < len(case_values):
                                result = (case_values[ci] >> bit_idx) & 1
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
        is_onehot = (s_net.width == count)

        for bit_idx in range(width):
            candidates: list[tuple] = []
            if is_onehot:
                # One-hot: each S bit selects one case
                for sel_idx in range(count):
                    case_net = cell.inputs.get(f"I{sel_idx}")
                    if case_net is None:
                        continue
                    case_bits = self._get_bits(case_net)
                    case_bit = case_bits[bit_idx] if bit_idx < len(case_bits) else "0"
                    sel_bit = s_bits[sel_idx] if sel_idx < len(s_bits) else "0"
                    candidates.append((sel_bit, case_bit))
            else:
                # Binary: build EQ comparison for each case index
                for sel_idx in range(count):
                    case_net = cell.inputs.get(f"I{sel_idx}")
                    if case_net is None:
                        continue
                    case_bits = self._get_bits(case_net)
                    case_bit = case_bits[bit_idx] if bit_idx < len(case_bits) else "0"
                    # EQ: S == sel_idx
                    eq_out = self.nl.alloc_bit()
                    self._build_eq_const(s_bits, sel_idx, eq_out)
                    candidates.append((eq_out, case_bit))

            default_bit = default_bits[bit_idx] if bit_idx < len(default_bits) else "0"

            if not candidates:
                if bit_idx < len(out_bits):
                    out_ecp5 = self._get_net(out_net)
                    self._set_bit(out_ecp5.bits, bit_idx, default_bit)
                continue

            current = default_bit
            for sel_bit, case_bit in candidates:
                mux_out = self.nl.alloc_bit()
                lut = self.nl.add_cell(self._fresh_name("pmux"), "LUT4")
                if cell.src:
                    lut.attributes["src"] = cell.src
                lut.parameters["INIT"] = "1110010011100100"
                lut.ports["A"] = [sel_bit]
                lut.ports["B"] = [current]
                lut.ports["C"] = [case_bit]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [mux_out]
                current = mux_out

            if bit_idx < len(out_bits):
                out_ecp5 = self._get_net(out_net)
                self._set_bit(out_ecp5.bits, bit_idx, current)

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
            new = a_bits[i % len(a_bits)] if a_bits else "0"
            self._set_bit(out_ecp5.bits, i, new)

    def _build_eq_const(self, signal_bits: list, const_val: int, out_bit: int) -> None:
        """Build EQ comparison: signal == const_val, result in out_bit."""
        width = len(signal_bits)
        # Per-bit XNOR, then AND tree
        match_bits: list[int | str] = []
        for i in range(width):
            expected = (const_val >> i) & 1
            sig_bit = signal_bits[i] if i < len(signal_bits) else "0"
            if isinstance(sig_bit, str):
                # Constant signal bit
                match_bits.append("1" if (sig_bit == "1") == (expected == 1) else "0")
            elif expected == 1:
                match_bits.append(sig_bit)  # XNOR(sig, 1) = sig
            else:
                # XNOR(sig, 0) = NOT(sig)
                inv = self.nl.alloc_bit()
                lut = self.nl.add_cell(self._fresh_name("eqinv"), "LUT4")
                lut.parameters["INIT"] = "0101010101010101"
                lut.ports["A"] = [sig_bit]
                lut.ports["B"] = ["0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [inv]
                match_bits.append(inv)
        # AND reduction
        while len(match_bits) > 1:
            nxt: list[int | str] = []
            for i in range(0, len(match_bits), 4):
                chunk = match_bits[i:i+4]
                # Filter out constant "1"s
                var_bits = [b for b in chunk if b != "1"]
                if not var_bits:
                    nxt.append("1")
                elif len(var_bits) == 1:
                    nxt.append(var_bits[0])
                else:
                    out = self.nl.alloc_bit()
                    lut = self.nl.add_cell(self._fresh_name("eqand"), "LUT4")
                    inits = {2: "1000100010001000", 3: "1000000010000000",
                             4: "1000000000000000"}
                    lut.parameters["INIT"] = inits[len(var_bits)]
                    for ci, cb in enumerate(var_bits):
                        lut.ports["ABCD"[ci]] = [cb]
                    for ci in range(len(var_bits), 4):
                        lut.ports["ABCD"[ci]] = ["1"]
                    lut.ports["Z"] = [out]
                    nxt.append(out)
            match_bits = nxt
        # Final result
        if match_bits and match_bits[0] != out_bit:
            if match_bits[0] == "1":
                # Always true — wire out_bit to VCC
                # Create a buffer LUT
                lut = self.nl.add_cell(self._fresh_name("eqtrue"), "LUT4")
                lut.parameters["INIT"] = "1111111111111111"
                lut.ports["A"] = ["0"]
                lut.ports["B"] = ["0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [out_bit]
            else:
                # Wire through buffer
                lut = self.nl.add_cell(self._fresh_name("eqbuf"), "LUT4")
                lut.parameters["INIT"] = "1010101010101010"
                lut.ports["A"] = [match_bits[0]]
                lut.ports["B"] = ["0"]
                lut.ports["C"] = ["0"]
                lut.ports["D"] = ["0"]
                lut.ports["Z"] = [out_bit]

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

            # Wire address port A (read) — offset by DATA_WIDTH mode.
            # DP16KD uses the upper address pins for the row address and
            # the lower pins for sub-word byte select within wider modes:
            #   X1:  ADA[13:0] = addr[13:0]   (offset 0)
            #   X2:  ADA[13:1] = addr[12:0]   (offset 1)
            #   X4:  ADA[13:2] = addr[11:0]   (offset 2)
            #   X9:  ADA[13:3] = addr[10:0]   (offset 3)
            #   X18: ADA[13:4] = addr[9:0]    (offset 4)
            #   X36: ADA[13:5] = addr[8:0]    (offset 5)
            _addr_offset = {1: 0, 2: 1, 4: 2, 9: 3, 18: 4, 36: 5}.get(data_width, 4)
            # The frontend creates numbered RADDR ports (RADDR1, RADDR2, ...)
            # for multiple read accesses. Use the first available one.
            raddr_net = cell.inputs.get("RADDR")
            if raddr_net is None:
                for _rk in sorted(cell.inputs):
                    if _rk.startswith("RADDR"):
                        raddr_net = cell.inputs[_rk]
                        break
            raddr_bits = self._get_bits(raddr_net) if raddr_net else []
            for i in range(14):
                logical = i - _addr_offset
                bit = raddr_bits[logical] if 0 <= logical < len(raddr_bits) else "0"
                bram.ports[f"ADA{i}"] = [bit]

            # Wire address port B (write) — same offset
            waddr_net = cell.inputs.get("WADDR")
            waddr_bits = self._get_bits(waddr_net) if waddr_net else []
            for i in range(14):
                logical = i - _addr_offset
                bit = waddr_bits[logical] if 0 <= logical < len(waddr_bits) else "0"
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
            # Wire extra RDATA outputs to same DOA bits
            _extra_rdata = [
                out for name, out in cell.outputs.items()
                if name.startswith("RDATA") and out is not rdata_net
            ]
            for _extra in _extra_rdata:
                _ebits = self._get_bits(_extra)
                for i in range(min(len(_ebits), len(rdata_bits))):
                    buf = self.nl.add_cell(self._fresh_name("bram_rd"), "LUT4")
                    buf.parameters["INIT"] = "1010101010101010"
                    buf.ports["A"] = [rdata_bits[i]]
                    buf.ports["B"] = ["0"]
                    buf.ports["C"] = ["0"]
                    buf.ports["D"] = ["0"]
                    buf.ports["Z"] = [_ebits[i]]
            return

        if bram_config == "DP16KD_TILED":
            # Large memory: tile into DP16KD grid.
            # Choose the narrowest data width that avoids depth tiling.
            # X4 mode (4096 deep) is preferred for large arrays like
            # program memory because it avoids the depth MUX overhead.
            mem_depth = int(cell.params.get("depth", 0))
            mem_width = int(cell.params.get("width", 0))
            # Pick the widest data width where depth fits in one tile row
            # (avoids depth MUX). Start wide, stop at first fit.
            # For 4096×32: X4 mode (4096 deep, 4 wide) → 8 BRAMs, no depth MUX.
            _dw_configs = [
                (36, 512), (18, 1024), (9, 2048),
                (4, 4096), (2, 8192), (1, 16384),
            ]
            tile_data_width = 4  # default: X4 is a good compromise
            tile_depth = 4096
            for _dw, _dd in _dw_configs:
                if mem_depth <= _dd:
                    tile_data_width = _dw
                    tile_depth = _dd
                    break
            tiles_wide = (mem_width + tile_data_width - 1) // tile_data_width
            tiles_deep = (mem_depth + tile_depth - 1) // tile_depth

            init_data: dict[int, int] = {}
            init_file = cell.params.get("init_file")
            if init_file:
                from nosis.readmem import parse_readmemh, parse_readmemb
                from pathlib import Path
                init_path = Path(init_file)
                if init_path.exists():
                    fmt = cell.params.get("init_format", "hex")
                    init_data = parse_readmemb(init_path) if fmt == "bin" else parse_readmemh(init_path)

            raddr_net = cell.inputs.get("RADDR")
            if raddr_net is None:
                for _rk in sorted(cell.inputs):
                    if _rk.startswith("RADDR"):
                        raddr_net = cell.inputs[_rk]
                        break
            waddr_net = cell.inputs.get("WADDR")
            wdata_net = cell.inputs.get("WDATA")
            we_net = cell.inputs.get("WE")
            clk_net = cell.inputs.get("CLK")
            rdata_net = list(cell.outputs.values())[0] if cell.outputs else None

            raddr_bits = self._get_bits(raddr_net) if raddr_net else []
            waddr_bits = self._get_bits(waddr_net) if waddr_net else []
            wdata_bits = self._get_bits(wdata_net) if wdata_net else []
            we_bits = self._get_bits(we_net) if we_net else ["0"]
            clk_bits = self._get_bits(clk_net) if clk_net else ["0"]
            rdata_bits = self._get_bits(rdata_net) if rdata_net else []

            # Collect ALL RDATA output nets — they all need to read from
            # the same BRAM DOA. The first one gets the DOA bits directly;
            # the rest get buffer LUTs connecting to the same bits.
            _extra_rdata_nets = [
                out for name, out in cell.outputs.items()
                if name.startswith("RDATA") and out is not rdata_net
            ]

            # For depth > 1: each depth tile gets separate output nets.
            # A MUX selects the correct tile based on high address bits.
            # tile_outputs[drow][global_bit_idx] = bit reference
            import math
            addr_bits_per_tile = int(math.log2(tile_depth)) if tile_depth > 1 else 0
            tile_outputs: list[list[int | str]] = []

            for drow in range(tiles_deep):
                row_outputs: list[int | str] = []
                for wcol in range(tiles_wide):
                    bram = self.nl.add_cell(self._fresh_name("bram"), "DP16KD")
                    if cell.src:
                        bram.attributes["src"] = cell.src
                    bram.parameters["DATA_WIDTH_A"] = str(tile_data_width)
                    bram.parameters["DATA_WIDTH_B"] = str(tile_data_width)
                    bram.parameters["REGMODE_A"] = "NOREG"
                    bram.parameters["REGMODE_B"] = "NOREG"
                    bram.parameters["CSDECODE_A"] = "0b000"
                    bram.parameters["CSDECODE_B"] = "0b000"
                    bram.parameters["WRITEMODE_A"] = "NORMAL"
                    bram.parameters["WRITEMODE_B"] = "NORMAL"
                    bram.parameters["GSR"] = "DISABLED"

                    from nosis.readmem import readmem_to_dp16kd_initvals
                    tile_init: dict[int, int] = {}
                    for local_addr in range(tile_depth):
                        global_addr = drow * tile_depth + local_addr
                        if global_addr in init_data:
                            full_word = init_data[global_addr]
                            bit_lo = wcol * tile_data_width
                            tile_val = (full_word >> bit_lo) & ((1 << tile_data_width) - 1)
                            if tile_val:
                                tile_init[local_addr] = tile_val
                    initvals = readmem_to_dp16kd_initvals(
                        tile_init, data_width=tile_data_width, depth=tile_depth
                    )
                    for k, v in initvals.items():
                        bram.parameters[k] = v

                    _tile_addr_offset = {1: 0, 2: 1, 4: 2, 9: 3, 18: 4, 36: 5}.get(tile_data_width, 4)
                    for i in range(14):
                        logical = i - _tile_addr_offset
                        bit = raddr_bits[logical] if 0 <= logical < len(raddr_bits) else "0"
                        bram.ports[f"ADA{i}"] = [bit]
                    for i in range(14):
                        logical = i - _tile_addr_offset
                        bit = waddr_bits[logical] if 0 <= logical < len(waddr_bits) else "0"
                        bram.ports[f"ADB{i}"] = [bit]

                    for i in range(18):
                        global_bit = wcol * tile_data_width + i
                        bit = wdata_bits[global_bit] if global_bit < len(wdata_bits) else "0"
                        bram.ports[f"DIB{i}"] = [bit]
                    for i in range(18):
                        bram.ports[f"DIA{i}"] = ["0"]

                    # Each tile gets its own output bits
                    tile_out_bits: list[int | str] = []
                    for i in range(tile_data_width):
                        b = self.nl.alloc_bit()
                        tile_out_bits.append(b)
                    for i in range(18):
                        bram.ports[f"DOA{i}"] = [tile_out_bits[i] if i < len(tile_out_bits) else self.nl.alloc_bit()]
                    for i in range(18):
                        bram.ports[f"DOB{i}"] = [self.nl.alloc_bit()]

                    # Extend row_outputs with this tile's bits
                    row_outputs.extend(tile_out_bits)

                    bram.ports["CLKA"] = [clk_bits[0] if clk_bits else "0"]
                    bram.ports["CLKB"] = [clk_bits[0] if clk_bits else "0"]
                    bram.ports["WEA"] = ["0"]
                    bram.ports["WEB"] = [we_bits[0] if we_bits else "0"]
                    bram.ports["CSA0"] = ["1"]
                    bram.ports["CSA1"] = ["0"]
                    bram.ports["CSA2"] = ["0"]
                    bram.ports["CSB0"] = ["1"]
                    bram.ports["CSB1"] = ["0"]
                    bram.ports["CSB2"] = ["0"]
                    bram.ports["RSTA"] = ["0"]
                    bram.ports["RSTB"] = ["0"]
                    bram.ports["OCEA"] = ["1"]
                    bram.ports["OCEB"] = ["1"]
                    bram.ports["CEA"] = ["1"]
                    bram.ports["CEB"] = ["1"]

                tile_outputs.append(row_outputs)

            # Wire final outputs: if only 1 depth tile, connect directly.
            # If multiple depth tiles, add LUT-based mux on high address bits.
            if tiles_deep == 1:
                for i in range(min(len(rdata_bits), len(tile_outputs[0]))):
                    # Direct connection: rewire rdata bit to tile output
                    rdata_bits[i]  # just verify it exists
                    # The rdata_bits are already allocated; we need to make the
                    # tile output drive them. Add a buffer LUT.
                    lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                    lut.parameters["INIT"] = "1010101010101010"  # pass-through on A
                    lut.ports["A"] = [tile_outputs[0][i]]
                    lut.ports["B"] = ["0"]
                    lut.ports["C"] = ["0"]
                    lut.ports["D"] = ["0"]
                    lut.ports["Z"] = [rdata_bits[i]]
            else:
                # Depth mux: select based on high address bits
                sel_bits = raddr_bits[addr_bits_per_tile:addr_bits_per_tile + tiles_deep.bit_length()]
                for bit_idx in range(min(len(rdata_bits), mem_width)):
                    if tiles_deep == 2:
                        # Simple 2:1 mux via LUT
                        sel = sel_bits[0] if sel_bits else "0"
                        a_bit = tile_outputs[0][bit_idx] if bit_idx < len(tile_outputs[0]) else "0"
                        b_bit = tile_outputs[1][bit_idx] if bit_idx < len(tile_outputs[1]) else "0"
                        lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                        # MUX: sel=0 -> A, sel=1 -> B
                        # LUT(A=tile0, B=tile1, C=sel, D=0)
                        # INIT: for each (D,C,B,A): output = A if C=0, B if C=1
                        # C=0: output=A -> bits 0,1,4,5 = A pattern = 1010
                        # C=1: output=B -> bits 2,3,6,7 = B pattern = 1100
                        # INIT = 1110010011100100 = 0xCACA
                        lut.parameters["INIT"] = "1110010011100100"
                        lut.ports["A"] = [a_bit]
                        lut.ports["B"] = [b_bit]
                        lut.ports["C"] = [sel]
                        lut.ports["D"] = ["0"]
                        lut.ports["Z"] = [rdata_bits[bit_idx]]
                    elif tiles_deep <= 4:
                        # 4:1 mux via LUT4
                        s0 = sel_bits[0] if len(sel_bits) > 0 else "0"
                        s1 = sel_bits[1] if len(sel_bits) > 1 else "0"
                        bits = [tile_outputs[d][bit_idx] if d < tiles_deep and bit_idx < len(tile_outputs[d]) else "0" for d in range(4)]
                        # Build 4:1 mux truth table: sel = {D=s1, C=s0}
                        init = 0
                        for d in range(2):
                            for c in range(2):
                                tile_sel = c + d * 2
                                for b in range(2):
                                    for a in range(2):
                                        idx = d * 8 + c * 4 + b * 2 + a
                                        # Output comes from the selected tile
                                        # We'll use a pair of LUTs for this
                                        pass
                        # For 4:1, use two levels of 2:1 mux LUTs
                        # Level 1: mux01 = sel0 ? tile1 : tile0
                        mux01 = self.nl.alloc_bit()
                        lut1 = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                        lut1.parameters["INIT"] = "1110010011100100"
                        lut1.ports["A"] = [bits[0]]
                        lut1.ports["B"] = [bits[1]]
                        lut1.ports["C"] = [s0]
                        lut1.ports["D"] = ["0"]
                        lut1.ports["Z"] = [mux01]
                        # Level 1: mux23 = sel0 ? tile3 : tile2
                        mux23 = self.nl.alloc_bit()
                        lut2 = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                        lut2.parameters["INIT"] = "1110010011100100"
                        lut2.ports["A"] = [bits[2]]
                        lut2.ports["B"] = [bits[3]]
                        lut2.ports["C"] = [s0]
                        lut2.ports["D"] = ["0"]
                        lut2.ports["Z"] = [mux23]
                        # Level 2: final = sel1 ? mux23 : mux01
                        lut3 = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                        lut3.parameters["INIT"] = "1110010011100100"
                        lut3.ports["A"] = [mux01]
                        lut3.ports["B"] = [mux23]
                        lut3.ports["C"] = [s1]
                        lut3.ports["D"] = ["0"]
                        lut3.ports["Z"] = [rdata_bits[bit_idx]]
                    else:
                        # >4 depth tiles: cascade mux trees (rare for ECP5-25F)
                        # For now, connect only the first tile
                        lut = self.nl.add_cell(self._fresh_name("lut"), "LUT4")
                        lut.parameters["INIT"] = "1010101010101010"
                        lut.ports["A"] = [tile_outputs[0][bit_idx] if bit_idx < len(tile_outputs[0]) else "0"]
                        lut.ports["B"] = ["0"]
                        lut.ports["C"] = ["0"]
                        lut.ports["D"] = ["0"]
                        lut.ports["Z"] = [rdata_bits[bit_idx]]

            # Wire extra RDATA outputs to the same DOA bits via buffer LUTs.
            # Multiple read ports all see the same BRAM data.
            for _extra_net in _extra_rdata_nets:
                _extra_bits = self._get_bits(_extra_net)
                for i in range(min(len(_extra_bits), len(rdata_bits))):
                    buf = self.nl.add_cell(self._fresh_name("bram_rd"), "LUT4")
                    buf.parameters["INIT"] = "1010101010101010"  # pass-through
                    buf.ports["A"] = [rdata_bits[i]]
                    buf.ports["B"] = ["0"]
                    buf.ports["C"] = ["0"]
                    buf.ports["D"] = ["0"]
                    buf.ports["Z"] = [_extra_bits[i]]
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

        # No BRAM/DPR tag — FF-based memory (register file).
        # Compile write ports per element, then build read MUX tree.
        depth = int(cell.params.get("depth", 0))
        width = int(cell.params.get("width", 0))
        if depth == 0 or width == 0:
            self._map_unknown(cell)
            return

        # Gather write ports: classify as constant-addr or variable-addr.
        const_writes: dict[int, list[tuple]] = {}  # {addr: [(wdata_bits, we_bits)]}
        var_writes: list[tuple] = []  # [(waddr_bits, wdata_bits, we_bits)]

        def _collect_write(wa_net, wd_net, we_net):
            wa_bits = self._get_bits(wa_net)
            wd_bits = self._get_bits(wd_net)
            we_bits = self._get_bits(we_net) if we_net else ["0"]
            # Check if address is a constant
            if all(isinstance(b, str) and b in ("0", "1") for b in wa_bits):
                addr_val = sum((1 if b == "1" else 0) << i for i, b in enumerate(wa_bits))
                const_writes.setdefault(addr_val, []).append((wd_bits, we_bits))
            else:
                var_writes.append((wa_bits, wd_bits, we_bits))

        wa0 = cell.inputs.get("WADDR")
        wd0 = cell.inputs.get("WDATA")
        we0 = cell.inputs.get("WE")
        if wa0 and wd0 and we0:
            _collect_write(wa0, wd0, we0)
        for idx in range(500):
            wa = cell.inputs.get(f"WADDR{idx}")
            wd = cell.inputs.get(f"WDATA{idx}")
            if not wa or not wd:
                if idx > 0:
                    break
                continue
            we_key = f"WE{idx}" if f"WE{idx}" in cell.inputs else "WE"
            we = cell.inputs.get(we_key)
            _collect_write(wa, wd, we)

        clk_net = cell.inputs.get("CLK")
        clk_bits = self._get_bits(clk_net) if clk_net else ["0"]

        # Build per-element D inputs from write ports.
        # Constant-address writes target specific elements directly.
        # Variable-address writes add per-element MUX gated by address match.
        word_q: list[list[int | str]] = []
        for w_idx in range(depth):
            q_bits: list[int | str] = []
            for b_idx in range(width):
                q_bit = self.nl.alloc_bit()
                current_d = q_bit  # hold = Q feedback

                # Apply constant-address writes targeting this element
                for wd_bits, we_bits in const_writes.get(w_idx, []):
                    we_bit = we_bits[0] if we_bits else "0"
                    wd_bit = wd_bits[b_idx] if b_idx < len(wd_bits) else "0"
                    mux_out = self.nl.alloc_bit()
                    lut = self.nl.add_cell(self._fresh_name("mmux"), "LUT4")
                    lut.parameters["INIT"] = "1110010011100100"
                    lut.ports["A"] = [we_bit]
                    lut.ports["B"] = [current_d]
                    lut.ports["C"] = [wd_bit]
                    lut.ports["D"] = ["0"]
                    lut.ports["Z"] = [mux_out]
                    current_d = mux_out

                # Apply variable-address writes (need addr comparator)
                for wa_bits, wd_bits, we_bits in var_writes:
                    # Build (WE && WADDR==w_idx) using a single LUT4 per addr bit
                    addr_w = len(wa_bits)
                    match_bits: list[int | str] = []
                    for a_i in range(addr_w):
                        expected = (w_idx >> a_i) & 1
                        ab = wa_bits[a_i] if a_i < len(wa_bits) else "0"
                        if expected == 1:
                            match_bits.append(ab)
                        else:
                            inv = self.nl.alloc_bit()
                            lut = self.nl.add_cell(self._fresh_name("minv"), "LUT4")
                            lut.parameters["INIT"] = "0101010101010101"
                            lut.ports["A"] = [ab]
                            lut.ports["B"] = ["0"]
                            lut.ports["C"] = ["0"]
                            lut.ports["D"] = ["0"]
                            lut.ports["Z"] = [inv]
                            match_bits.append(inv)
                    we_bit = we_bits[0] if we_bits else "0"
                    all_match = [we_bit] + match_bits
                    while len(all_match) > 1:
                        nxt: list[int | str] = []
                        for i in range(0, len(all_match), 4):
                            chunk = all_match[i:i+4]
                            if len(chunk) == 1:
                                nxt.append(chunk[0])
                            else:
                                out = self.nl.alloc_bit()
                                lut = self.nl.add_cell(self._fresh_name("mand"), "LUT4")
                                inits = {2: "1000100010001000", 3: "1000000010000000",
                                         4: "1000000000000000"}
                                lut.parameters["INIT"] = inits[len(chunk)]
                                for ci, cb in enumerate(chunk):
                                    lut.ports["ABCD"[ci]] = [cb]
                                for ci in range(len(chunk), 4):
                                    lut.ports["ABCD"[ci]] = ["1"]
                                lut.ports["Z"] = [out]
                                nxt.append(out)
                        all_match = nxt
                    wen = all_match[0]
                    wd_bit = wd_bits[b_idx] if b_idx < len(wd_bits) else "0"
                    mux_out = self.nl.alloc_bit()
                    lut = self.nl.add_cell(self._fresh_name("mmux"), "LUT4")
                    lut.parameters["INIT"] = "1110010011100100"
                    lut.ports["A"] = [wen]
                    lut.ports["B"] = [current_d]
                    lut.ports["C"] = [wd_bit]
                    lut.ports["D"] = ["0"]
                    lut.ports["Z"] = [mux_out]
                    current_d = mux_out

                ff = self.nl.add_cell(self._fresh_name("mff"), "TRELLIS_FF")
                ff.parameters["GSR"] = "DISABLED"
                ff.parameters["CEMUX"] = "1 "
                ff.parameters["CLKMUX"] = "CLK"
                ff.parameters["LSRMUX"] = "LSR"
                ff.parameters["REGSET"] = "RESET"
                ff.parameters["SRMODE"] = "LSR_OVER_CE"
                ff.ports["CLK"] = [clk_bits[0] if clk_bits else "0"]
                ff.ports["DI"] = [current_d]
                ff.ports["LSR"] = ["0"]
                ff.ports["Q"] = [q_bit]
                q_bits.append(q_bit)
            word_q.append(q_bits)

        # Read logic: binary MUX tree keyed on RADDR
        for rdata_net in cell.outputs.values():
            rdata_bits = self._get_bits(rdata_net)
            rdata_name = None
            for oname, onet in cell.outputs.items():
                if onet is rdata_net:
                    rdata_name = oname
                    break
            raddr_key = rdata_name.replace("RDATA", "RADDR") if rdata_name else "RADDR"
            raddr_net_ir = cell.inputs.get(raddr_key)
            if raddr_net_ir is None:
                for rk in sorted(cell.inputs):
                    if rk.startswith("RADDR"):
                        raddr_net_ir = cell.inputs[rk]
                        break
            raddr_bits = self._get_bits(raddr_net_ir) if raddr_net_ir else []
            for b_idx in range(width):
                if b_idx >= len(rdata_bits):
                    break
                candidates = [word_q[w][b_idx] if w < len(word_q) else "0" for w in range(depth)]
                level = candidates
                for a_i in range(len(raddr_bits)):
                    if len(level) <= 1:
                        break
                    nxt: list[int | str] = []
                    sel = raddr_bits[a_i] if a_i < len(raddr_bits) else "0"
                    for j in range(0, len(level), 2):
                        lo = level[j]
                        hi = level[j+1] if j+1 < len(level) else lo
                        if lo == hi:
                            nxt.append(lo)
                        else:
                            mux_out = self.nl.alloc_bit()
                            lut = self.nl.add_cell(self._fresh_name("mrd"), "LUT4")
                            lut.parameters["INIT"] = "1110010011100100"
                            lut.ports["A"] = [sel]
                            lut.ports["B"] = [lo]
                            lut.ports["C"] = [hi]
                            lut.ports["D"] = ["0"]
                            lut.ports["Z"] = [mux_out]
                            nxt.append(mux_out)
                    level = nxt
                if level:
                    self._set_bit(rdata_bits, b_idx, level[0])

    def _map_unknown(self, cell: Cell) -> None:
        """Emit a placeholder for unsupported operations."""
        for out_net in cell.outputs.values():
            ecp5_net = self._get_net(out_net)
            ecp5_net.bits = ["0"] * out_net.width


def _dead_cell_eliminate(netlist: ECP5Netlist) -> int:
    """Remove ECP5 cells whose outputs are not referenced by any other cell or port."""
    # Collect all bit references from cell inputs and module ports
    used_bits: set[int] = set()
    for port_info in netlist.ports.values():
        for b in port_info.get("bits", []):
            if isinstance(b, int):
                used_bits.add(b)
    for cell in netlist.cells.values():
        for port_name, bits in cell.ports.items():
            # Use port_directions if available, otherwise assume input
            # for non-output ports (conservative)
            pd = cell.attributes.get("_port_dir_" + port_name, "")
            if not pd:
                # Determine from port name convention: known outputs
                _out_names = {"Z", "F", "F0", "F1", "S0", "S1", "COUT", "Q",
                              "P", "PPOUT", "CO", "OFX0", "OFX1"}
                pd = "output" if port_name in _out_names else "input"
            if pd != "output":
                for b in bits:
                    if isinstance(b, int):
                        used_bits.add(b)

    # Remove cells whose outputs are all unreferenced
    to_remove = []
    for name, cell in netlist.cells.items():
        if cell.cell_type == "TRELLIS_FF":
            continue  # keep all FFs (they have side effects)
        out_bits = set()
        for port_name, bits in cell.ports.items():
            if port_name in ("Z", "F", "S0", "S1", "COUT", "Q"):
                for b in bits:
                    if isinstance(b, int):
                        out_bits.add(b)
        if out_bits and not (out_bits & used_bits):
            to_remove.append(name)

    for name in to_remove:
        del netlist.cells[name]
    return len(to_remove)


def map_to_ecp5(design: Design) -> ECP5Netlist:
    """Map a Nosis IR Design to an ECP5 netlist."""
    mod = design.top_module()
    netlist = ECP5Netlist(top=mod.name)
    mapper = _ECP5Mapper(netlist)
    mapper.map_module(mod)

    # Apply wiring aliases: wiring cells (CONCAT/SLICE/etc) overwrote
    # ECP5Net.bits in-place, orphaning original allocated bit integers.
    # Replace all orphaned references using the recorded alias map.
    alias = mapper._bit_alias
    # Add aliases from bit_origin: if an allocated bit's net position
    # now holds a different value, that's an alias.
    for orig_bit, (net_name, idx) in netlist._bit_origin.items():
        if orig_bit in alias:
            continue
        net = netlist.nets.get(net_name)
        if net and idx < len(net.bits) and net.bits[idx] != orig_bit:
            alias[orig_bit] = net.bits[idx]
    # Resolve transitive aliases (A→B, B→C becomes A→C)
    changed = True
    while changed:
        changed = False
        for k, v in list(alias.items()):
            if v in alias:
                alias[k] = alias[v]
                changed = True
    _OUT_PORTS = {"Z", "Q", "S0", "S1", "COUT", "F", "F0", "F1", "OFX0", "OFX1",
                  "PPOUT", "CO", "CDIVX", "DCSOUT", "CLKO"}
    _OUT_PORTS.update(f"DOA{i}" for i in range(18))
    _OUT_PORTS.update(f"DOB{i}" for i in range(18))
    _OUT_PORTS.update(f"DO{i}" for i in range(4))
    _OUT_PORTS.update(f"P{i}" for i in range(36))
    _OUT_PORTS.update(f"R{i}" for i in range(54))
    if alias:
        for cell in netlist.cells.values():
            for port, bits in cell.ports.items():
                if port in _OUT_PORTS:
                    continue
                for i, b in enumerate(bits):
                    if b in alias:
                        bits[i] = alias[b]
        for pi in netlist.ports.values():
            bits = pi.get("bits", [])
            for i, b in enumerate(bits):
                if b in alias:
                    bits[i] = alias[b]
        for net in netlist.nets.values():
            for i, b in enumerate(net.bits):
                if b in alias:
                    net.bits[i] = alias[b]

    # Fix remaining orphaned bits by building a reverse lookup:
    # for each ECP5Net, map its bit positions to the actual driven bits.
    driven_bits: set[int] = set()
    for cell in netlist.cells.values():
        for port, bits in cell.ports.items():
            if port in _OUT_PORTS:
                for b in bits:
                    if isinstance(b, int):
                        driven_bits.add(b)
    for pi in netlist.ports.values():
        if pi.get("direction") == "input":
            for b in pi.get("bits", []):
                if isinstance(b, int):
                    driven_bits.add(b)

    # Build: for each net, record which bit positions map to which driven bits
    net_bit_map: dict[str, dict[int, int | str]] = {}
    for name, net in netlist.nets.items():
        m = {}
        for i, b in enumerate(net.bits):
            m[i] = b
        net_bit_map[name] = m

    # For each orphaned input bit, find the net it belongs to and
    # get the current driven bit at that position.
    # Resolve orphaned bits using the original allocation record.
    bit_origin = getattr(netlist, '_bit_origin', {})
    # Build set of IR nets that have live driver cells
    live_nets: set[str] = set()
    for cell_obj in mod.cells.values():
        for out_net in cell_obj.outputs.values():
            live_nets.add(out_net.name)
    for pname in mod.ports:
        live_nets.add(pname)

    fixed = 0
    for cell in netlist.cells.values():
        for port, bits in cell.ports.items():
            if port in _OUT_PORTS:
                continue
            for i, b in enumerate(bits):
                if isinstance(b, int) and b >= 2 and b not in driven_bits:
                    resolved = False
                    origin = bit_origin.get(b)
                    if origin:
                        net_name, bit_idx = origin
                        ecp5_net = netlist.nets.get(net_name)
                        if ecp5_net and bit_idx < len(ecp5_net.bits):
                            actual = ecp5_net.bits[bit_idx]
                            if actual != b:
                                bits[i] = actual
                                fixed += 1
                                resolved = True
                        if not resolved and net_name not in live_nets:
                            bits[i] = "0"
                            fixed += 1
                            resolved = True

    # Fix CCU2C D0/D1/CIN: resolve unresolved integer bits to their
    # current values in the net bit lists.
    all_bits: dict[int, int | str] = {}
    for net in netlist.nets.values():
        for b in net.bits:
            if isinstance(b, int) and b >= 2:
                pass  # signal bit - maps to itself
            elif isinstance(b, str):
                pass  # constant
    # Build: original_allocated_bit -> current_net_value
    # by scanning all nets for position mappings
    for name, net in netlist.nets.items():
        ecp5 = mapper._net_map.get(name)
        if ecp5 is not net:
            continue
        for i, b in enumerate(net.bits):
            all_bits[i] = b  # not useful without original

    # Simpler: for each CCU2C D/A/B/C port with an undriven integer,
    # check if it's an orphaned bit and resolve via ir_net_bits.
    for cell in netlist.cells.values():
        if cell.cell_type != "CCU2C":
            continue
        for port in ("D0", "D1", "A0", "A1", "B0", "B1", "C0", "C1", "CIN"):
            bits = cell.ports.get(port, [])
            for i, b in enumerate(bits):
                if isinstance(b, int) and b >= 2 and b not in driven_bits:
                    info = ir_net_bits.get(b)
                    if info:
                        net_name, bit_idx = info
                        ecp5_net = netlist.nets.get(net_name)
                        if ecp5_net and bit_idx < len(ecp5_net.bits):
                            actual = ecp5_net.bits[bit_idx]
                            if actual != b:
                                bits[i] = actual
        # Fix CIN for first-in-chain ADD cells
        cin = cell.ports.get("CIN", ["0"])[0]
        if cin == "0":
            init0 = cell.parameters.get("INIT0", "")
            if init0 == "1001011010101010":
                d0 = cell.ports.get("D0", ["0"])[0]
                if d0 == "1":
                    cell.ports["CIN"] = ["1"]

    # Re-resolve FF DI/CLK/LSR ports: cells mapped before their input
    # MUX chain cells reference stale bits from original net allocation.
    # Build a comprehensive stale→current map from all nets.
    stale_to_current: dict[int, int | str] = {}
    for net in netlist.nets.values():
        for orig_bit, (net_name, idx) in netlist._bit_origin.items():
            if net_name == net.name and idx < len(net.bits):
                cur = net.bits[idx]
                if cur != orig_bit:
                    stale_to_current[orig_bit] = cur
    # Add alias entries
    for k, v in alias.items():
        if isinstance(k, int) and k >= 2:
            stale_to_current[k] = v
    # Apply to ALL FF input ports (DI, CLK, LSR, CE)
    for cell in netlist.cells.values():
        if cell.cell_type != "TRELLIS_FF":
            continue
        for port_name in ("DI", "CLK", "LSR", "CE"):
            bits = cell.ports.get(port_name, [])
            for i, b in enumerate(bits):
                if isinstance(b, int) and b in stale_to_current:
                    bits[i] = stale_to_current[b]

    # Remove LUT4 cells with constant Z outputs — dead cells that
    # conflict with the JSON backend's GND/VCC tie cells.
    dead_luts = [name for name, cell in netlist.cells.items()
                 if cell.cell_type == "LUT4"
                 and all(isinstance(b, str) for b in cell.ports.get("Z", []))]
    for name in dead_luts:
        del netlist.cells[name]

    return netlist
