// Test: tri-state / inout port
module tristate (
    input wire clk,
    input wire oe,
    input wire [7:0] data_out,
    output wire [7:0] data_in,
    inout wire [7:0] data_bus
);
    assign data_bus = oe ? data_out : 8'bz;
    assign data_in = data_bus;
endmodule
