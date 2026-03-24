"""Nosis clock domain analysis — identify clock domains and detect crossings.

Example::

    from nosis.clocks import analyze_clock_domains

    domains, crossings = analyze_clock_domains(mod)
    for domain in domains:
        print(f"Clock {domain.clock_net}: {len(domain.ff_cells)} FFs")
    for crossing in crossings:
        print(f"CDC: {crossing.source_domain} -> {crossing.dest_domain}")

Scans the IR for FF cells and groups them by their CLK input net.
Each unique CLK net defines a clock domain. When a net crosses from
one domain to another (an FF in domain A drives combinational logic
that feeds an FF in domain B), it is flagged as a clock domain crossing.

This is an analysis pass — it does not modify the IR.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nosis.ir import Cell, Module, Net, PrimOp

__all__ = [
    "ClockDomain",
    "ClockCrossing",
    "analyze_clock_domains",
    "insert_synchronizers",
]


@dataclass(slots=True)
class ClockDomain:
    """A group of FFs sharing the same clock net."""
    clock_net: str
    ff_cells: list[str] = field(default_factory=list)
    output_nets: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ClockCrossing:
    """A net that crosses from one clock domain to another."""
    net: str
    source_domain: str
    dest_domain: str
    source_ff: str
    dest_ff: str


def analyze_clock_domains(mod: Module) -> tuple[list[ClockDomain], list[ClockCrossing]]:
    """Identify clock domains and detect crossings.

    Returns ``(domains, crossings)``.
    """
    # Group FFs by clock net
    domain_map: dict[str, ClockDomain] = {}
    ff_to_domain: dict[str, str] = {}

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        clk_net = cell.inputs.get("CLK")
        if clk_net is None:
            continue
        clk_name = clk_net.name
        if clk_name not in domain_map:
            domain_map[clk_name] = ClockDomain(clock_net=clk_name)
        domain_map[clk_name].ff_cells.append(cell.name)
        ff_to_domain[cell.name] = clk_name

        # Track output nets
        for out_net in cell.outputs.values():
            domain_map[clk_name].output_nets.append(out_net.name)

    domains = list(domain_map.values())

    # Detect crossings: for each FF, trace its D input backward.
    # If we find a net driven by an FF in a different domain, that's a crossing.
    crossings: list[ClockCrossing] = []
    seen_crossings: set[tuple[str, str, str]] = set()

    for cell in mod.cells.values():
        if cell.op != PrimOp.FF:
            continue
        dest_domain = ff_to_domain.get(cell.name)
        if dest_domain is None:
            continue

        d_net = cell.inputs.get("D")
        if d_net is None:
            continue

        # Walk backward through combinational logic
        visited: set[str] = set()
        worklist: list[Net] = [d_net]
        while worklist:
            net = worklist.pop()
            if net.name in visited:
                continue
            visited.add(net.name)

            if net.driver is None:
                continue
            driver = net.driver
            if driver.op == PrimOp.FF:
                source_domain = ff_to_domain.get(driver.name)
                if source_domain and source_domain != dest_domain:
                    key = (net.name, source_domain, dest_domain)
                    if key not in seen_crossings:
                        seen_crossings.add(key)
                        crossings.append(ClockCrossing(
                            net=net.name,
                            source_domain=source_domain,
                            dest_domain=dest_domain,
                            source_ff=driver.name,
                            dest_ff=cell.name,
                        ))
                continue  # Don't walk through FFs

            for inp_net in driver.inputs.values():
                if inp_net.name not in visited:
                    worklist.append(inp_net)

    return domains, crossings


def insert_synchronizers(mod: Module, crossings: list[ClockCrossing]) -> int:
    """Insert 2-FF synchronizer cells at clock domain crossings.

    For each crossing, inserts two back-to-back FFs clocked by the
    destination domain's clock. The first FF captures the asynchronous
    signal, the second resolves metastability.

    Returns the number of synchronizer pairs inserted.
    """
    inserted = 0
    counter = 0

    for crossing in crossings:
        dest_ff = mod.cells.get(crossing.dest_ff)
        if dest_ff is None:
            continue
        dest_clk = dest_ff.inputs.get("CLK")
        if dest_clk is None:
            continue
        crossing_net = mod.nets.get(crossing.net)
        if crossing_net is None:
            continue

        counter += 1
        # Sync FF stage 1
        sync1_q = mod.add_net(f"_sync1_q_{counter}", crossing_net.width)
        sync1 = mod.add_cell(f"_sync1_{counter}", PrimOp.FF)
        mod.connect(sync1, "CLK", dest_clk)
        mod.connect(sync1, "D", crossing_net)
        mod.connect(sync1, "Q", sync1_q, direction="output")
        sync1.attributes["cdc_sync"] = "stage1"

        # Sync FF stage 2
        sync2_q = mod.add_net(f"_sync2_q_{counter}", crossing_net.width)
        sync2 = mod.add_cell(f"_sync2_{counter}", PrimOp.FF)
        mod.connect(sync2, "CLK", dest_clk)
        mod.connect(sync2, "D", sync1_q)
        mod.connect(sync2, "Q", sync2_q, direction="output")
        sync2.attributes["cdc_sync"] = "stage2"

        # Rewire destination FF's D input through the synchronizer
        d_net = dest_ff.inputs.get("D")
        if d_net and d_net.name == crossing.net:
            dest_ff.inputs["D"] = sync2_q
        else:
            # Crossing goes through combinational logic — rewire the
            # first consumer of the crossing net in the destination domain
            for other in mod.cells.values():
                if other is dest_ff or other is sync1 or other is sync2:
                    continue
                for pname, pnet in list(other.inputs.items()):
                    if pnet.name == crossing.net:
                        other.inputs[pname] = sync2_q
        inserted += 1

    return inserted
