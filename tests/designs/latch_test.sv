// Test: latch inference from incomplete if in always_comb
module latch_test (
    input wire en,
    input wire [7:0] d,
    output logic [7:0] q
);
    always_comb begin
        if (en)
            q = d;
        // no else — infers latch
    end
endmodule
