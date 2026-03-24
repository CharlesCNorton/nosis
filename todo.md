# TODO

## RTL coverage — arbitrary SystemVerilog

1. Tri-state / `inout` at the RTL level — frontend doesn't lower bidirectional assignments
2. Multi-dimensional array writes — `array[i][j] <= val` not handled
3. `$clog2`, `$bits` at non-constant contexts — parametric designs may not resolve at elaboration
4. Interfaces and modports — members extracted but modport direction constraints not enforced
5. Multi-clock domain synthesis — single-clock works, multi-clock needs domain tracking through pipeline
6. Latch synthesis — warn but also produce TRELLIS_FF in latch mode (LSRMUX/SRMODE)

## Optimization — reduce LUT inflation from correct MUX lowering

7. Mutual exclusion detection — if-else branches that are provably exclusive should share one MUX, not chain
8. Conditional guard folding — MUX(cond, hold, val) where hold is never selected can drop the guard

## Hardware targets — OS and system support

9. MMU / PMP support in techmap — Physical Memory Protection for process isolation
10. Interrupt controller synthesis — priority encoder, vectored interrupt table
11. DMA controller patterns — burst transfer FSMs, address generators
12. Timer/counter synthesis — watchdog, system tick, PWM patterns
