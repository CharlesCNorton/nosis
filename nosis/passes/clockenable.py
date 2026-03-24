"""Clock-enable extraction.

A register whose next-state mux tree holds its own value in some branches
(`state <= cond ? next : state`) is emitted as a flip-flop plus a feedback
mux that costs one LUT per bit. The ECP5 flip-flop has a native clock-enable
input, so the hold can be free: register updates only when some condition
fires. This pass rewrites `FF(D = mux_tree_holding_Q)` into
`FF(D = update, CE = enable)`, where `enable` is the condition under which the
register changes and `update` is the mux tree with the hold-Q branches removed.

The transform is per-subtree: for a node `n`, `extract(n)` returns
`(enable, update)` such that `n == (enable ? update : Q)`. A pure-hold subtree
returns `(None, None)`. This holds regardless of the conditions above `n`, so
results are memoised across the shared Q leaves.
"""

from __future__ import annotations

from nosis.ir import Module, Net, PrimOp

__all__ = ["extract_clock_enables"]


def extract_clock_enables(mod: Module) -> int:
    """Rewrite hold-mux flip-flops to use a native clock enable.

    Returns the number of flip-flops given a clock enable.
    """
    count = 0
    ctr = [0]

    def _fresh(prefix: str, width: int) -> tuple[Net, str]:
        ctr[0] += 1
        return mod.add_net(f"$ce_{prefix}_{ctr[0]}", width), f"$ce_{prefix}_{ctr[0]}"

    def _const(value: int, width: int = 1) -> Net:
        net, name = _fresh("c", width)
        c = mod.add_cell(name.replace("$ce_c", "$ce_const"), PrimOp.CONST, value=value, width=width)
        mod.connect(c, "Y", net, direction="output")
        return net

    def _unary(op: PrimOp, a: Net, width: int) -> Net:
        net, name = _fresh("u", width)
        c = mod.add_cell(name.replace("$ce_u", "$ce_op"), op)
        mod.connect(c, "A", a)
        mod.connect(c, "Y", net, direction="output")
        return net

    def _binary(op: PrimOp, a: Net, b: Net, width: int) -> Net:
        net, name = _fresh("b", width)
        c = mod.add_cell(name.replace("$ce_b", "$ce_op"), op)
        mod.connect(c, "A", a)
        mod.connect(c, "B", b)
        mod.connect(c, "Y", net, direction="output")
        return net

    def _mux(s: Net, a: Net, b: Net, width: int) -> Net:
        net, name = _fresh("m", width)
        c = mod.add_cell(name.replace("$ce_m", "$ce_mux"), PrimOp.MUX)
        mod.connect(c, "S", s)
        mod.connect(c, "A", a)
        mod.connect(c, "B", b)
        mod.connect(c, "Y", net, direction="output")
        return net

    def _is_const1(net: Net | None) -> bool:
        return (net is not None and net.driver is not None
                and net.driver.op == PrimOp.CONST
                and int(net.driver.params.get("value", 0)) == 1)

    ffs = [c for c in mod.cells.values() if c.op == PrimOp.FF]
    for cell in ffs:
        if "CE" in cell.inputs:
            continue
        d = cell.inputs.get("D")
        outs = list(cell.outputs.values())
        if d is None or not outs or d.driver is None or d.driver.op != PrimOp.MUX:
            continue
        q = outs[0]
        tgt = str(cell.params.get("ff_target", ""))
        dwidth = q.width

        def _is_hold(net: Net) -> bool:
            return net is q or net.name == q.name or (bool(tgt) and net.name == tgt)

        def _has_hold(net: Net, depth: int, seen: set) -> bool:
            if net is None or depth > 80:
                return False
            if _is_hold(net):
                return True
            dr = net.driver
            if dr is None or dr.op != PrimOp.MUX or id(dr) in seen:
                return False
            seen.add(id(dr))
            return (_has_hold(dr.inputs.get("A"), depth + 1, seen)
                    or _has_hold(dr.inputs.get("B"), depth + 1, seen))

        if not _has_hold(d, 0, set()):
            continue

        memo: dict[int, tuple[Net | None, Net | None]] = {}
        bailed = [False]

        def _extract(net: Net, depth: int) -> tuple[Net | None, Net | None]:
            # Returns (enable, update): net == (enable ? update : Q).
            # (None, None) means a pure hold (net == Q for all inputs).
            if net is None or depth > 80:
                bailed[0] = True
                return (_const(1), net)
            if _is_hold(net):
                return (None, None)
            key = id(net)
            if key in memo:
                return memo[key]
            dr = net.driver
            if dr is None or dr.op != PrimOp.MUX:
                res = (_const(1), net)
                memo[key] = res
                return res
            s = dr.inputs.get("S")
            a = dr.inputs.get("A")
            b = dr.inputs.get("B")
            if s is None or a is None or b is None:
                bailed[0] = True
                res = (_const(1), net)
                memo[key] = res
                return res
            ea, ua = _extract(a, depth + 1)
            eb, ub = _extract(b, depth + 1)
            a_hold = ea is None
            b_hold = eb is None
            if a_hold and b_hold:
                res: tuple[Net | None, Net | None] = (None, None)
            elif a_hold:
                # net == S ? (eb?ub:Q) : Q  -> update when S & eb
                enable = s if _is_const1(eb) else _binary(PrimOp.AND, s, eb, 1)
                res = (enable, ub)
            elif b_hold:
                ns = _unary(PrimOp.NOT, s, 1)
                enable = ns if _is_const1(ea) else _binary(PrimOp.AND, ns, ea, 1)
                res = (enable, ua)
            else:
                enable = _mux(s, ea, eb, 1)
                update = _mux(s, ua, ub, dwidth)
                res = (enable, update)
            memo[key] = res
            return res

        enable, update = _extract(d, 0)
        if bailed[0] or enable is None or update is None or _is_const1(enable):
            continue

        cell.inputs["CE"] = enable
        cell.inputs["D"] = update
        count += 1

    return count
