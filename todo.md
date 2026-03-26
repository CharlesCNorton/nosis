# TODO

Third-party RTL coverage — everything needed to handle arbitrary SystemVerilog.

1. Tri-state / `inout` at the RTL level — frontend doesn't lower bidirectional assignments
2. `for` loops in always blocks — runtime for-loops need unrolling (generate-for handled by slang)
3. Multi-dimensional array writes — `array[i][j] <= val` not handled
4. `casez` / `casex` wildcard matching — slang resolves some but not all cases
5. `$clog2`, `$bits` at non-constant contexts — parametric designs may not resolve at elaboration
6. Task and function calls — user-defined functions in always blocks need inlining
7. Interfaces and modports — members extracted but modport direction constraints not enforced
8. Assertions and coverpoints — should strip with warnings, not crash
9. `real` and `shortreal` types — need clean rejection with error message
10. Multi-clock domain synthesis — single-clock works, multi-clock needs domain tracking through pipeline
11. Latch synthesis — warn but also produce TRELLIS_FF in latch mode (LSRMUX/SRMODE)
12. Gate-level primitives — `and`, `or`, `nand`, `buf` Verilog gate instantiations
13. UDP (User-Defined Primitives) — rare but legal, need clean rejection or basic support
14. `specify` blocks — timing constraints in RTL, should strip with warning
15. Memory inference for large arrays — arrays > 32 elements should use DP16KD, not individual FFs
