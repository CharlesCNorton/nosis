# TODO

Third-party RTL coverage — everything needed to handle arbitrary SystemVerilog.

1. Tri-state / `inout` at the RTL level — frontend doesn't lower bidirectional assignments
2. Multi-dimensional array writes — `array[i][j] <= val` not handled
3. `$clog2`, `$bits` at non-constant contexts — parametric designs may not resolve at elaboration
4. Interfaces and modports — members extracted but modport direction constraints not enforced
5. `real` and `shortreal` types — need clean rejection with error message
6. Multi-clock domain synthesis — single-clock works, multi-clock needs domain tracking through pipeline
7. Latch synthesis — warn but also produce TRELLIS_FF in latch mode (LSRMUX/SRMODE)
8. UDP (User-Defined Primitives) — rare but legal, need clean rejection or basic support
9. Memory inference for large arrays — arrays > 32 elements should use DP16KD, not individual FFs
