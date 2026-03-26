// Test: for loops in always blocks
module for_loop (
    input wire clk,
    input wire rst,
    input wire [7:0] data_in,
    output logic [7:0] data_out,
    output logic [3:0] popcount
);
    // Runtime for-loop in always_comb — popcount
    always_comb begin
        popcount = 4'd0;
        for (int i = 0; i < 8; i = i + 1)
            popcount = popcount + {3'd0, data_in[i]};
    end

    // For-loop in always_ff — shift register
    logic [7:0] shift_reg;
    always_ff @(posedge clk) begin
        if (rst)
            shift_reg <= 8'd0;
        else begin
            for (int i = 7; i > 0; i = i - 1)
                shift_reg[i] <= shift_reg[i-1];
            shift_reg[0] <= data_in[0];
        end
    end
    assign data_out = shift_reg;
endmodule
