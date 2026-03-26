// Test: casez wildcard matching
module casez_test (
    input wire [3:0] opcode,
    output logic [1:0] category
);
    always_comb begin
        casez (opcode)
            4'b0???: category = 2'd0;  // top bit 0
            4'b10??: category = 2'd1;  // top bits 10
            4'b110?: category = 2'd2;  // top bits 110
            4'b1110: category = 2'd3;  // exact match
            default: category = 2'd0;
        endcase
    end
endmodule
