// 18x18 unsigned multiply — should infer MULT18X18D on ECP5
module mul_test (
    input  wire        clk,
    input  wire [17:0] a,
    input  wire [17:0] b,
    output reg  [35:0] p
);
    always @(posedge clk)
        p <= a * b;
endmodule
