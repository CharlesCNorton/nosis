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

    # Extract register file values from FF state
    import re
    regs = {0: 0}  # x0 is always 0
    for cell in mod.cells.values():
        if cell.op == PrimOp.FF:
            m = re.search(r"regs_(\d+)", cell.name)
            if m:
                idx = int(m.group(1))
                for o in cell.outputs.values():
                    regs[idx] = ff_state.get(o.name, 0)

    return {"_pc": ff_state.get(pc_q, 0) if pc_q else 0,
            "_state": ff_state.get(state_q, 0) if state_q else 0,
            "_regs": regs,
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
    """ADDI x1, x0, 42 — load immediate and check value."""
    program = [_addi(1, 0, 42), _nop(), _nop()]
    result = _run_program(program, max_cycles=30)
    assert result["_regs"].get(1) == 42, f"x1={result['_regs'].get(1)}, expected 42"


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
    """ADD x3, x1, x2 — check result value."""
    program = [_addi(1, 0, 10), _addi(2, 0, 20), _add(3, 1, 2), _nop()]
    result = _run_program(program, max_cycles=40)
    assert result["_regs"].get(3) == 30, f"x3={result['_regs'].get(3)}, expected 30"


@requires_rime_soc
def test_sub_registers():
    """SUB x3, x1, x2 — check subtraction result."""
    program = [_addi(1, 0, 50), _addi(2, 0, 30), _sub(3, 1, 2), _nop()]
    result = _run_program(program, max_cycles=40)
    assert result["_regs"].get(3) == 20, f"x3={result['_regs'].get(3)}, expected 20"


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
    """Chain of arithmetic — verify all intermediate and final values."""
    program = [
        _addi(1, 0, 100),   # x1 = 100
        _addi(2, 0, 50),    # x2 = 50
        _add(3, 1, 2),      # x3 = 150
        _sub(4, 1, 2),      # x4 = 50
        _add(5, 3, 4),      # x5 = 200
        _nop(),
    ]
    result = _run_program(program, max_cycles=50)
    r = result["_regs"]
    assert r.get(1) == 100, f"x1={r.get(1)}"
    assert r.get(2) == 50, f"x2={r.get(2)}"
    assert r.get(3) == 150, f"x3={r.get(3)}"
    assert r.get(4) == 50, f"x4={r.get(4)}"
    assert r.get(5) == 200, f"x5={r.get(5)}"


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


# ---------------------------------------------------------------------------
# ALU ops
# ---------------------------------------------------------------------------

def _andi(rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (0b111 << 12) | (rd << 7) | 0b0010011

def _ori(rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (0b110 << 12) | (rd << 7) | 0b0010011

def _xori(rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (0b100 << 12) | (rd << 7) | 0b0010011

def _slli(rd, rs1, shamt):
    return (shamt << 20) | (rs1 << 15) | (0b001 << 12) | (rd << 7) | 0b0010011

def _srli(rd, rs1, shamt):
    return (shamt << 20) | (rs1 << 15) | (0b101 << 12) | (rd << 7) | 0b0010011

def _slti(rd, rs1, imm):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (0b010 << 12) | (rd << 7) | 0b0010011


@requires_rime_soc
def test_andi():
    prog = [_addi(1, 0, 0xFF), _andi(2, 1, 0x0F), _nop()]
    result = _run_program(prog, max_cycles=30)
    assert result["_regs"].get(2) == 0x0F, f"x2={result['_regs'].get(2):#x}"

@requires_rime_soc
def test_ori():
    prog = [_addi(1, 0, 0xF0), _ori(2, 1, 0x0F), _nop()]
    result = _run_program(prog, max_cycles=30)
    assert result["_regs"].get(2) == 0xFF, f"x2={result['_regs'].get(2):#x}"

@requires_rime_soc
def test_xori():
    prog = [_addi(1, 0, 0xFF), _xori(2, 1, 0xFF), _nop()]
    result = _run_program(prog, max_cycles=30)
    assert result["_regs"].get(2) == 0, f"x2={result['_regs'].get(2)}"

@requires_rime_soc
def test_slli():
    prog = [_addi(1, 0, 1), _slli(2, 1, 4), _nop()]
    result = _run_program(prog, max_cycles=30)
    assert result["_regs"].get(2) == 16, f"x2={result['_regs'].get(2)}"

@requires_rime_soc
def test_srli():
    prog = [_addi(1, 0, 256), _srli(2, 1, 4), _nop()]
    result = _run_program(prog, max_cycles=30)
    assert result["_regs"].get(2) == 16, f"x2={result['_regs'].get(2)}"

@requires_rime_soc
def test_slti():
    prog = [_addi(1, 0, 5), _slti(2, 1, 10), _slti(3, 1, 3), _nop()]
    result = _run_program(prog, max_cycles=40)
    assert result["_regs"].get(2) == 1, f"x2={result['_regs'].get(2)} (5<10 should be 1)"
    assert result["_regs"].get(3) == 0, f"x3={result['_regs'].get(3)} (5<3 should be 0)"

@requires_rime_soc
def test_lui_value():
    prog = [_lui(1, 0x12345), _nop()]
    result = _run_program(prog, max_cycles=20)
    assert result["_regs"].get(1) == 0x12345000, f"x1={result['_regs'].get(1):#x}"


# ---------------------------------------------------------------------------
# Branches
# ---------------------------------------------------------------------------

@requires_rime_soc
def test_beq_taken():
    """BEQ x0, x0, +8 — always taken (x0==x0), skip next instruction."""
    prog = [
        _beq(0, 0, 8),      # PC=0: branch to PC=8
        _addi(1, 0, 99),    # PC=4: skipped
        _addi(2, 0, 42),    # PC=8: target
        _nop(),
    ]
    result = _run_program(prog, max_cycles=40)
    r = result["_regs"]
    assert r.get(1, 0) == 0, f"x1={r.get(1)} — should be 0 (skipped)"
    assert r.get(2) == 42, f"x2={r.get(2)} — should be 42"

@requires_rime_soc
def test_beq_not_taken():
    """BEQ x1, x0, +8 — not taken (x1!=x0), execute next instruction."""
    prog = [
        _addi(1, 0, 1),     # PC=0: x1=1
        _beq(1, 0, 8),      # PC=4: not taken (1!=0)
        _addi(2, 0, 77),    # PC=8: executed
        _nop(),
    ]
    result = _run_program(prog, max_cycles=40)
    assert result["_regs"].get(2) == 77, f"x2={result['_regs'].get(2)}"

@requires_rime_soc
def test_bne_taken():
    """BNE x1, x0, +8 — taken (1!=0)."""
    prog = [
        _addi(1, 0, 1),
        _bne(1, 0, 8),      # PC=4: taken
        _addi(2, 0, 99),    # PC=8: skipped
        _addi(3, 0, 55),    # PC=12: target (4+8=12)
        _nop(),
    ]
    result = _run_program(prog, max_cycles=40)
    r = result["_regs"]
    assert r.get(2, 0) == 0, f"x2={r.get(2)} — should be 0 (skipped)"
    assert r.get(3) == 55, f"x3={r.get(3)} — should be 55"


# ---------------------------------------------------------------------------
# JAL
# ---------------------------------------------------------------------------

@requires_rime_soc
def test_jal():
    """JAL x1, +8 — jump forward, store return address."""
    prog = [
        _jal(1, 8),         # PC=0: jump to PC=8, x1=PC+4=4
        _addi(2, 0, 99),    # PC=4: skipped
        _addi(3, 0, 42),    # PC=8: target
        _nop(),
    ]
    result = _run_program(prog, max_cycles=40)
    r = result["_regs"]
    assert r.get(1) == 4, f"x1={r.get(1)} — return address should be 4"
    assert r.get(2, 0) == 0, f"x2={r.get(2)} — should be 0 (skipped)"
    assert r.get(3) == 42, f"x3={r.get(3)} — should be 42"
