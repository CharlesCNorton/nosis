// Test: Verilog gate-level primitives
module gate_prims (
    input wire a, b, c,
    output wire y_and,
    output wire y_or,
    output wire y_nand,
    output wire y_buf,
    output wire y_not
);
    and  g1 (y_and, a, b);
    or   g2 (y_or, a, b, c);
    nand g3 (y_nand, a, b);
    buf  g4 (y_buf, a);
    not  g5 (y_not, a);
endmodule
