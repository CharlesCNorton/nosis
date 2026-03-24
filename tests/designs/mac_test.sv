// Multiply-accumulate: acc += a * b — should infer ALU54B on ECP5
module mac_test (
    input  wire        clk,
    input  wire        rst,
    input  wire [15:0] a,
    input  wire [15:0] b,
    output reg  [35:0] acc
);
    always @(posedge clk) begin
        if (rst)
            acc <= 0;
        else
            acc <= acc + a * b;
    end
endmodule
