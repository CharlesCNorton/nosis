// Test: user-defined function calls in always blocks
module func_call (
    input wire [7:0] a, b,
    output logic [7:0] result
);
    function automatic [7:0] add_saturate(input [7:0] x, input [7:0] y);
        logic [8:0] sum;
        sum = {1'b0, x} + {1'b0, y};
        if (sum[8])
            return 8'hFF;
        else
            return sum[7:0];
    endfunction

    always_comb begin
        result = add_saturate(a, b);
    end
endmodule
