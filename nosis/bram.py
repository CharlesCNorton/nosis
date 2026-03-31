"""Nosis BRAM inference — recognize array patterns and emit DP16KD instances.

Scans the IR for MEMORY cells (arrays inferred from behavioral HDL) and
determines whether they can be mapped to ECP5 DP16KD block RAMs.

DP16KD is a true dual-port 16Kbit BRAM:
  - Configurable as 16Kx1, 8Kx2, 4Kx4, 2Kx9, 1Kx18, 512x36
  - Two independent read/write ports
  - Synchronous read and write
"""

from __future__ import annotations

from nosis.ir import Module, PrimOp

__all__ = [
    "infer_brams",
    "infer_memory_ports",
    "detect_write_mode",
    "infer_output_register",
]


def _compile_multi_write(mod: Module, cell) -> None:
    """Compile multiple WADDR/WDATA/WE ports into a single effective port.

    Multi-write MEMORY cells have separate (WADDR_i, WDATA_i, WE_i) for
    each case branch write. DPR16X4 accepts only one write port. This
    pass MUXes the addresses and data into one effective triple using
    the per-write WE signals as select conditions.

    Priority: later writes (higher index) override earlier ones.
    """
    # Collect all write ports
    ports: list[tuple] = []  # [(waddr_key, wdata_key, we_key)]
    for i in range(500):
        wak = f"WADDR{i}" if i > 0 else "WADDR"
        wdk = f"WDATA{i}" if i > 0 else "WDATA"
        wek = f"WE{i}" if i > 0 else "WE"
        if wak not in cell.inputs:
            if i > 1:
                break
            continue
        ports.append((wak, wdk, wek))

    if len(ports) <= 1:
        return  # nothing to compile

    # Build MUX chain: start from first port, each subsequent overrides
    width = int(cell.params.get("width", 8))
    first_wa = cell.inputs[ports[0][0]]
    first_wd = cell.inputs[ports[0][1]]
    cur_addr = first_wa
    cur_data = first_wd

    from nosis.ir import Net

    for wak, wdk, wek in ports[1:]:
        wa = cell.inputs.get(wak)
        wd = cell.inputs.get(wdk)
        we = cell.inputs.get(wek)
        if not wa or not wd or not we:
            continue

        # MUX: if WE_i active, use this port's addr/data, else keep previous
        addr_w = max(wa.width, cur_addr.width)
        mux_addr = mod.add_net(f"_mwmux_a_{cell.name}_{wak}", addr_w)
        mux_a = mod.add_cell(f"_mwmux_a_{cell.name}_{wak}", PrimOp.MUX)
        mod.connect(mux_a, "S", we)
        mod.connect(mux_a, "A", cur_addr)
        mod.connect(mux_a, "B", wa)
        mod.connect(mux_a, "Y", mux_addr, direction="output")

        mux_data = mod.add_net(f"_mwmux_d_{cell.name}_{wdk}", width)
        mux_d = mod.add_cell(f"_mwmux_d_{cell.name}_{wdk}", PrimOp.MUX)
        mod.connect(mux_d, "S", we)
        mod.connect(mux_d, "A", cur_data)
        mod.connect(mux_d, "B", wd)
        mod.connect(mux_d, "Y", mux_data, direction="output")

        cur_addr = mux_addr
        cur_data = mux_data

    # Replace all WADDR/WDATA ports with the single compiled port
    for wak, wdk, wek in ports:
        if wak in cell.inputs:
            del cell.inputs[wak]
        if wdk in cell.inputs:
            del cell.inputs[wdk]
        if wek in cell.inputs and wek != "WE":
            del cell.inputs[wek]

    cell.inputs["WADDR"] = cur_addr
    cell.inputs["WDATA"] = cur_data
    # WE stays as the OR of all conditions (already computed)


def _fits_dp16kd(depth: int, width: int) -> tuple[int, int] | None:
    """Check if array dimensions fit a DP16KD configuration.

    Returns ``(addr_bits, data_width)`` for the best-fit config, or None.
    """
    configs = [
        (14, 1, 16384),   # 16Kx1
        (13, 2, 8192),    # 8Kx2
        (12, 4, 4096),    # 4Kx4
        (11, 9, 2048),    # 2Kx9 (8 data + 1 parity)
        (10, 18, 1024),   # 1Kx18 (16 data + 2 parity)
        (9, 36, 512),     # 512x36 (32 data + 4 parity)
    ]
    for addr_bits, data_width, max_depth in configs:
        if depth <= max_depth and width <= data_width:
            return addr_bits, data_width
    return None


def _count_brams_needed(depth: int, width: int) -> int:
    """Count how many DP16KD instances are needed for an array."""
    fit = _fits_dp16kd(depth, width)
    if fit is not None:
        return 1
    # Multiple BRAMs needed — width tiling
    best_data_width = 36  # widest single BRAM
    brams_wide = (width + best_data_width - 1) // best_data_width
    best_depth = 512  # depth for 36-wide
    brams_deep = (depth + best_depth - 1) // best_depth
    return brams_wide * brams_deep


def infer_brams(mod: Module) -> int:
    """Tag MEMORY cells that should become DP16KD instances.

    Adds ``bram_config`` to cell params for cells that qualify.
    Returns the number of memories tagged for BRAM inference.
    """
    tagged = 0

    for cell in list(mod.cells.values()):
        if cell.op != PrimOp.MEMORY:
            continue

        depth = int(cell.params.get("depth", 0))
        width = int(cell.params.get("width", 0))

        if depth <= 0 or width <= 0:
            continue

        total_bits = depth * width

        # DPR16X4 for small arrays (depth <= 16) with ONE write port.
        # Arrays with multiple constant-address writes (resp[0]=X,
        # resp[1]=Y in one cycle) cannot use DPR16X4 — it has one
        # write port and can only write one address per clock.
        # These fall through to FF-based mapping below.
        if depth <= 16:
            waddr_count = sum(1 for k in cell.inputs if k.startswith("WADDR"))
            if waddr_count <= 1:
                tiles = (width + 3) // 4
                cell.params["bram_config"] = "DPR16X4"
                cell.params["bram_count"] = tiles
                tagged += 1
                continue

        import os
        _bram_threshold = int(os.environ.get("NOSIS_BRAM_THRESHOLD", "256"))
        if total_bits < _bram_threshold:
            continue

        # DP16KD with REGMODE_A="NOREG" provides read data within the
        # same clock cycle after the address is latched. This supports
        # combinational read patterns like `assign data = mem[addr]`
        # as long as the address is stable when the clock edge fires.

        fit = _fits_dp16kd(depth, width)
        if fit is not None:
            addr_bits, data_width = fit
            cell.params["bram_config"] = "DP16KD"
            cell.params["bram_addr_bits"] = addr_bits
            cell.params["bram_data_width"] = data_width
            cell.params["bram_count"] = 1
            tagged += 1
        else:
            count = _count_brams_needed(depth, width)
            if count <= 56:  # ECP5-25F has 56 BRAMs
                cell.params["bram_config"] = "DP16KD_TILED"
                cell.params["bram_count"] = count
                tagged += 1

    return tagged


def infer_memory_ports(mod: Module) -> int:
    """Infer read/write port patterns for MEMORY cells from the surrounding logic.

    Scans the IR for cells that read from or write to MEMORY cell addresses.
    Tags MEMORY cells with ``mem_read_ports`` and ``mem_write_ports`` counts
    derived from the number of distinct address nets connected to the cell.

    Returns the number of MEMORY cells annotated.
    """
    annotated = 0

    for cell in mod.cells.values():
        if cell.op != PrimOp.MEMORY:
            continue

        read_addrs: set[str] = set()
        write_addrs: set[str] = set()

        raddr = cell.inputs.get("RADDR")
        if raddr:
            read_addrs.add(raddr.name)

        waddr = cell.inputs.get("WADDR")
        if waddr:
            write_addrs.add(waddr.name)

        cell.params["mem_read_ports"] = len(read_addrs)
        cell.params["mem_write_ports"] = len(write_addrs)
        cell.params["mem_dual_port"] = len(read_addrs) > 0 and len(write_addrs) > 0
        annotated += 1

    return annotated


def detect_write_mode(mod: Module) -> int:
    """Detect read-before-write vs write-before-read for MEMORY cells.

    When read and write addresses are the same net, the ordering determines
    the DP16KD WRITEMODE parameter:
    - NORMAL: read returns the old value (read-before-write)
    - WRITETHROUGH: read returns the new value (write-before-write)

    Heuristic: if the write data net is derived from the read data net
    (feedback loop), assume write-through. Otherwise assume read-first.

    Sets ``write_mode`` in cell params. Returns cells annotated.
    """
    annotated = 0

    for cell in mod.cells.values():
        if cell.op != PrimOp.MEMORY:
            continue

        raddr = cell.inputs.get("RADDR")
        waddr = cell.inputs.get("WADDR")
        wdata = cell.inputs.get("WDATA")
        rdata_nets = list(cell.outputs.values())

        mode = "NORMAL"

        if raddr and waddr and raddr.name == waddr.name:
            # Same address — check for feedback from rdata to wdata
            if wdata and rdata_nets:
                rdata = rdata_nets[0]
                # Walk backward from wdata to see if rdata is in the cone
                visited: set[str] = set()
                worklist = [wdata]
                found_feedback = False
                while worklist and not found_feedback:
                    net = worklist.pop()
                    if net.name in visited:
                        continue
                    visited.add(net.name)
                    if net.name == rdata.name:
                        found_feedback = True
                        break
                    if net.driver and net.driver.op not in (PrimOp.FF, PrimOp.INPUT, PrimOp.CONST):
                        for inp in net.driver.inputs.values():
                            if inp.name not in visited:
                                worklist.append(inp)
                if found_feedback:
                    mode = "WRITETHROUGH"

        cell.params["write_mode"] = mode
        annotated += 1

    return annotated


def infer_output_register(mod: Module) -> int:
    """Infer BRAM output registers when an FF directly reads the data port.

    When a MEMORY's read data output feeds directly into an FF's D input
    (same clock), the FF can be absorbed into the DP16KD by setting
    REGMODE to OUTREG. This saves a fabric FF.

    Sets ``output_register`` and ``output_ff`` in cell params.
    Returns the number of BRAMs annotated.
    """
    annotated = 0

    for cell in mod.cells.values():
        if cell.op != PrimOp.MEMORY:
            continue

        rdata_nets = list(cell.outputs.values())
        if not rdata_nets:
            continue
        rdata = rdata_nets[0]

        for other in mod.cells.values():
            if other.op != PrimOp.FF:
                continue
            d_net = other.inputs.get("D")
            if d_net and d_net.name == rdata.name:
                ff_clk = other.inputs.get("CLK")
                mem_clk = cell.inputs.get("CLK")
                if ff_clk and mem_clk and ff_clk.name == mem_clk.name:
                    cell.params["output_register"] = True
                    cell.params["output_ff"] = other.name
                    # Redirect consumers of the FF's Q to the BRAM's RDATA
                    # so the FF can be eliminated by DCE
                    for ff_out in other.outputs.values():
                        for consumer in mod.cells.values():
                            if consumer is other or consumer is cell:
                                continue
                            for pn, pnet in list(consumer.inputs.items()):
                                if pnet is ff_out:
                                    consumer.inputs[pn] = rdata
                        ff_out.driver = rdata.driver
                    other.inputs.clear()
                    other.outputs.clear()
                    other.op = PrimOp.CONST
                    other.params = {"value": 0, "width": 1, "_absorbed": True}
                    annotated += 1
                    break

    return annotated
