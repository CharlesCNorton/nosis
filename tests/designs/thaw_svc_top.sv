// Test wrapper for thaw_svc_minimal — instantiates UART + service as sub-instances.
module top (
    input  wire       clk, input  wire usb_rx, output wire usb_tx,
    output logic [4:0] led, input wire [1:0] button,
    output wire flash_csn, output wire flash_mosi, output wire flash_wpn, output wire flash_resetn, input wire flash_miso,
    output wire sd_clk, output wire sd_csn, output wire sd_mosi, input wire sd_miso, input wire sd_det,
    output wire sdram_clk, output wire sdram_cke, output wire sdram_csn,
    output wire sdram_rasn, output wire sdram_casn, output wire sdram_wen,
    output wire [1:0] sdram_ba, output wire [12:0] sdram_a, inout wire [15:0] sdram_dq, output wire [1:0] sdram_dqm
);
    assign flash_csn=1; assign flash_mosi=0; assign flash_wpn=1; assign flash_resetn=1;
    assign sd_clk=0; assign sd_csn=1; assign sd_mosi=1;
    assign sdram_clk=0; assign sdram_cke=0; assign sdram_csn=1;
    assign sdram_rasn=1; assign sdram_casn=1; assign sdram_wen=1;
    assign sdram_ba=0; assign sdram_a=0; assign sdram_dqm=2'b11;
    localparam integer CLK_HZ = 25000000;
    localparam integer BAUD = 115200;
    logic sys_clk;
    always_ff @(posedge clk) begin if (~button[0]) sys_clk<=0; else sys_clk<=~sys_clk; end
    logic [3:0] startup_cnt; logic startup_done;
    always_ff @(posedge sys_clk) begin
        if (~button[0]) begin startup_cnt<=0; startup_done<=0; end
        else if (!startup_done) begin
            if (startup_cnt==4'd15) startup_done<=1;
            else startup_cnt<=startup_cnt+1;
        end
    end
    wire rst = ~button[0] || !startup_done;
    wire rx_valid; wire [7:0] rx_data; wire tx_send; wire [7:0] tx_data_out;
    uart_rx #(.CLK(CLK_HZ),.BAUD_RATE(BAUD)) RX (.clk(sys_clk),.rx(usb_rx),.finish(rx_valid),.data(rx_data));
    uart_tx #(.CLK(CLK_HZ),.BAUD_RATE(BAUD)) TX (.clk(sys_clk),.send(tx_send),.data(tx_data_out),.tx(usb_tx));
    logic [15:0] tx_busy_counter;
    wire tx_busy = (tx_busy_counter != 16'd0);
    localparam integer UART_CHAR_CLKS = ((CLK_HZ / BAUD) * 11);
    always_ff @(posedge sys_clk) begin
        if (rst) tx_busy_counter <= 16'd0;
        else if (tx_send) tx_busy_counter <= UART_CHAR_CLKS[15:0];
        else if (tx_busy_counter != 16'd0) tx_busy_counter <= tx_busy_counter - 16'd1;
    end
    wire [4:0] svc_led;
    thaw_service #(.CLK_HZ(CLK_HZ)) SVC (
        .clk(sys_clk), .rst(rst),
        .uart_rx_valid(rx_valid), .uart_rx_data(rx_data),
        .uart_tx_send(tx_send), .uart_tx_data(tx_data_out), .uart_tx_busy(tx_busy),
        .spi_op(), .spi_addr(), .spi_prog_data(), .spi_start(),
        .spi_busy(1'b0), .spi_done(1'b0), .spi_ok(1'b0),
        .spi_read_data(128'd0), .spi_status(16'd0), .spi_jedec(24'd0),
        .sdram_start(), .sdram_wr(), .sdram_base_addr(), .sdram_wdata(),
        .sdram_rdata(128'd0), .sdram_done(1'b0), .sdram_busy(1'b0), .sdram_init_done(1'b0),
        .busy(), .gpio_led(svc_led)
    );
    logic [23:0] heartbeat;
    always_ff @(posedge sys_clk) begin if (rst) heartbeat<=0; else heartbeat<=heartbeat+1; end
    assign led = {heartbeat[23], svc_led[3:0]};
endmodule
