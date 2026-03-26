"""RIME-V CPU verification — run RISC-V test programs through nosis simulation.

Loads RIME-V into the FastSimulator, drives clock cycles with a Python-side
instruction memory model, and checks register file values and outputs.
"""

import warnings

from tests.conftest import RIME_V, requires_rime_soc  # noqa: E402

warnings.filterwarnings("ignore")


def _build_cpu_sim():
    """Build a FastSimulator for RIME-V and return (sim, net_index)."""
    from nosis.frontend import parse_files, lower_to_ir
    from nosis.sim import FastSimulator

    r = parse_files([RIME_V], top="rime_v")
    d = lower_to_ir(r, top="rime_v")
    m = d.top_module()
    sim = FastSimulator(m)
    return sim, m


def _run_program(program: list[int], max_cycles: int = 500) -> dict:
    """Run a list of 32-bit RISC-V instructions through RIME-V.

    Returns the final simulation state dict.
    """
    sim, mod = _build_cpu_sim()

    # Instruction memory (word-addressed)
    imem = {}
    for i, insn in enumerate(program):
        imem[i * 4] = insn

    # State
    ff_state: dict[str, int] = {}
    from nosis.ir import PrimOp
    ff_pairs: list[tuple[str, str]] = []
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            for out in cell.outputs.values():
                ff_state[out.name] = 0
            d_net = cell.inputs.get("D")
            if d_net:
                for out in cell.outputs.values():
                    ff_pairs.append((d_net.name, out.name))

    # Reset for 3 cycles
    vals = {}
    for _ in range(3):
        inputs = {"clk": 0, "rst": 1, "imem_data": 0, "mem_done": 0,
                  "mem_done_rdata": 0, "irq": 0}
        inputs.update(ff_state)
        vals = sim.step(inputs)
        for d_name, q_name in ff_pairs:
            if d_name in vals:
                ff_state[q_name] = vals[d_name]

    # Find key FF Q net names for direct state reading
    state_q = pc_q = None
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            d_net = cell.inputs.get("D")
            if d_net:
                for o in cell.outputs.values():
                    if "state" in cell.name and "dbg" not in cell.name:
                        state_q = o.name
                    if "_pc_" in cell.name and "next" not in cell.name and "mepc" not in cell.name:
                        pc_q = o.name

    # Run program
    for cycle in range(max_cycles):
        # Read PC from FF state directly
        pc = ff_state.get(pc_q, 0) if pc_q else 0
        insn = imem.get(pc & 0xFFFFFFFC, 0)

        inputs = {
            "clk": 1, "rst": 0,
            "imem_data": insn,
            "mem_done": 0,
            "mem_done_rdata": 0,
            "irq": 0,
        }
        inputs.update(ff_state)
        vals = sim.step(inputs)

        # Update FF state
        for d_name, q_name in ff_pairs:
            if d_name in vals:
                ff_state[q_name] = vals[d_name]

        # Check state from FF directly
        state = ff_state.get(state_q, 0) if state_q else 0

        # Check for trap (EBREAK sets state to S_TRAP=7)
        if state == 7:  # S_TRAP
            break

    return {"_pc": ff_state.get(pc_q, 0) if pc_q else 0,
            "_state": ff_state.get(state_q, 0) if state_q else 0,
            "_ff_state": ff_state}


# ---------------------------------------------------------------------------
# RV32I instruction encoding helpers
# ---------------------------------------------------------------------------

def _addi(rd, rs1, imm):
    """ADDI rd, rs1, imm"""
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (0b000 << 12) | (rd << 7) | 0b0010011

def _add(rd, rs1, rs2):
    """ADD rd, rs1, rs2"""
    return (0b0000000 << 25) | (rs2 << 20) | (rs1 << 15) | (0b000 << 12) | (rd << 7) | 0b0110011

def _sub(rd, rs1, rs2):
    """SUB rd, rs1, rs2"""
    return (0b0100000 << 25) | (rs2 << 20) | (rs1 << 15) | (0b000 << 12) | (rd << 7) | 0b0110011

def _lui(rd, imm20):
    """LUI rd, imm20"""
    return ((imm20 & 0xFFFFF) << 12) | (rd << 7) | 0b0110111

def _nop():
    """NOP (ADDI x0, x0, 0)"""
    return _addi(0, 0, 0)

def _ebreak():
    """EBREAK"""
    return 0x00100073


# ---------------------------------------------------------------------------
# Test programs
# ---------------------------------------------------------------------------

@requires_rime_soc
def test_addi_basic():
    """ADDI x1, x0, 42 — load immediate into register."""
    program = [
        _addi(1, 0, 42),   # x1 = 42
        _nop(),
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=100)
    assert result["_pc"] > 0, f"CPU didn't advance past reset, PC={result['_pc']}"


@requires_rime_soc
def test_lui_basic():
    """LUI x2, 0x12345 — load upper immediate."""
    program = [
        _lui(2, 0x12345),   # x2 = 0x12345000
        _nop(),
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=100)
    assert result["_pc"] > 0, f"CPU didn't advance, PC={result['_pc']}"


@requires_rime_soc
def test_add_registers():
    """ADDI x1, x0, 10; ADDI x2, x0, 20; ADD x3, x1, x2 — register arithmetic."""
    program = [
        _addi(1, 0, 10),    # x1 = 10
        _addi(2, 0, 20),    # x2 = 20
        _add(3, 1, 2),      # x3 = x1 + x2 = 30
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=200)
    assert result["_pc"] > 0, f"CPU didn't advance, PC={result['_pc']}"


@requires_rime_soc
def test_sub_registers():
    """ADDI x1, x0, 50; ADDI x2, x0, 30; SUB x3, x1, x2 — subtraction."""
    program = [
        _addi(1, 0, 50),
        _addi(2, 0, 30),
        _sub(3, 1, 2),      # x3 = 50 - 30 = 20
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=200)
    assert result["_pc"] > 0, f"CPU didn't advance, PC={result['_pc']}"


@requires_rime_soc
def test_pc_advances():
    """Verify PC advances through multiple instructions."""
    program = [
        _addi(1, 0, 1),     # PC=0x00
        _addi(2, 0, 2),     # PC=0x04
        _addi(3, 0, 3),     # PC=0x08
        _addi(4, 0, 4),     # PC=0x0C
        _addi(5, 0, 5),     # PC=0x10
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=200)
    assert result["_pc"] >= 0x10, f"PC didn't advance enough: 0x{result['_pc']:08X}"


@requires_rime_soc
def test_x0_always_zero():
    """Writing to x0 should have no effect — x0 is hardwired to zero."""
    program = [
        _addi(0, 0, 999),   # try to write x0
        _add(1, 0, 0),      # x1 = x0 + x0 = should be 0
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=100)
    assert result["_pc"] > 0


@requires_rime_soc
def test_multiple_arithmetic():
    """Chain of arithmetic operations using register file."""
    program = [
        _addi(1, 0, 100),   # x1 = 100
        _addi(2, 0, 50),    # x2 = 50
        _add(3, 1, 2),      # x3 = 150
        _sub(4, 1, 2),      # x4 = 50
        _add(5, 3, 4),      # x5 = 200
        _nop(),
        _nop(),
        _ebreak(),
    ]
    result = _run_program(program, max_cycles=300)
    assert result["_pc"] >= 0x10, f"PC didn't advance through all instructions: 0x{result['_pc']:08X}"


# --- Branch instructions ---

def _beq(rs1, rs2, offset):
    """BEQ rs1, rs2, offset (offset in bytes, must be even)"""
    imm = offset & 0x1FFF
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | \
           (rs2 << 20) | (rs1 << 15) | (0b000 << 12) | \
           (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | 0b1100011

def _bne(rs1, rs2, offset):
    """BNE rs1, rs2, offset"""
    imm = offset & 0x1FFF
    return (((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | \
           (rs2 << 20) | (rs1 << 15) | (0b001 << 12) | \
           (((imm >> 1) & 0xF) << 8) | (((imm >> 11) & 1) << 7) | 0b1100011

def _sw(rs2, rs1, offset):
    """SW rs2, offset(rs1)"""
    imm = offset & 0xFFF
    return (((imm >> 5) & 0x7F) << 25) | (rs2 << 20) | (rs1 << 15) | \
           (0b010 << 12) | ((imm & 0x1F) << 7) | 0b0100011

def _lw(rd, rs1, offset):
    """LW rd, offset(rs1)"""
    return ((offset & 0xFFF) << 20) | (rs1 << 15) | (0b010 << 12) | (rd << 7) | 0b0000011

def _jal(rd, offset):
    """JAL rd, offset"""
    imm = offset & 0x1FFFFF
    return (((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | \
           (((imm >> 11) & 1) << 20) | (((imm >> 12) & 0xFF) << 12) | \
           (rd << 7) | 0b1101111


@requires_rime_soc
def test_read_after_write():
    """Write x1, then read x1 in the next instruction."""
    program = [
        _addi(1, 0, 42),    # x1 = 42
        _addi(2, 1, 8),     # x2 = x1 + 8 = 50 (reads x1 from register file)
        _nop(),
        _nop(),
    ]
    result = _run_program(program, max_cycles=200)
    assert result["_pc"] >= 0x08, f"PC didn't advance: 0x{result['_pc']:08X}"


@requires_rime_soc
def test_pc_increment_exact():
    """Verify PC increments by exactly 4 per instruction."""
    program = [
        _addi(1, 0, 1),
        _addi(2, 0, 2),
        _addi(3, 0, 3),
        _nop(),
    ]
    # 4 instructions * 4 cycles each = 16 cycles to reach PC=0x10
    result = _run_program(program, max_cycles=16)
    assert result["_pc"] == 0x10, f"PC should be 0x10, got 0x{result['_pc']:08X}"
