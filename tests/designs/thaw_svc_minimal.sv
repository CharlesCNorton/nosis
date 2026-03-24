// Stripped-down thaw_service: VERSION and PING with TX FIFO drain.
//
// This is the canonical hardware regression test for nosis. It exercises:
//   - Cross-always_ff array access (drain reads tx_fifo written by state machine)
//   - Nested case statements (state -> cmd_reg dispatch)
//   - Variable-indexed array writes (tx_fifo[tx_wr] <= resp[resp_idx])
//   - Constant-indexed array writes (resp[0] <= value in each case branch)
//   - Wire declarations with initializers (wire tx_empty = tx_wr == tx_rd)
//   - TX busy counter with cross-always_ff variable read
//   - Sub-instance UART RX/TX modules
//
// If nosis can synthesize this correctly, it handles the full Thaw service.
module thaw_service #(parameter CLK_HZ = 25000000) (
    input  wire        clk,
    input  wire        rst,
    input  wire        uart_rx_valid,
    input  wire [7:0]  uart_rx_data,
    output logic       uart_tx_send,
    output logic [7:0] uart_tx_data,
    input  wire        uart_tx_busy,
    output logic       busy,
    output logic [4:0] gpio_led,
    // Stub ports (match full thaw_service interface)
    output logic [2:0] spi_op, output logic [23:0] spi_addr,
    output logic [127:0] spi_prog_data, output logic spi_start,
    input wire spi_busy, input wire spi_done, input wire spi_ok,
    input wire [127:0] spi_read_data, input wire [15:0] spi_status, input wire [23:0] spi_jedec,
    output logic sdram_start, output logic sdram_wr, output logic [24:0] sdram_base_addr,
    output logic [127:0] sdram_wdata,
    input wire [127:0] sdram_rdata, input wire sdram_done, input wire sdram_busy, input wire sdram_init_done
);
    localparam [7:0] CMD_VERSION = 8'h00;
    localparam [7:0] CMD_PING    = 8'h01;
    localparam [7:0] PHASE = 8'h05;
    localparam [7:0] VER   = 8'h01;
    localparam [7:0] ACK   = 8'hAC;

    logic [7:0] resp [0:2];
    logic [7:0] tx_fifo [0:3];
    logic [1:0] tx_wr, tx_rd;
    wire tx_empty = (tx_wr == tx_rd);
    logic [4:0] resp_len, resp_idx;
    logic [3:0] state;
    logic [7:0] cmd_reg;

    localparam S_IDLE     = 4'd0;
    localparam S_DISPATCH = 4'd1;
    localparam S_TX_RESP  = 4'd2;

    // TX FIFO drain — separate always_ff from state machine
    always_ff @(posedge clk) begin
        uart_tx_send <= 1'b0;
        if (rst) begin
            tx_rd <= 2'd0;
        end else if (!tx_empty && !uart_tx_busy && !uart_tx_send) begin
            uart_tx_data <= tx_fifo[tx_rd];
            uart_tx_send <= 1'b1;
            tx_rd <= tx_rd + 2'd1;
        end
    end

    // Main state machine
    always_ff @(posedge clk) begin
        if (rst) begin
            state <= S_IDLE;
            tx_wr <= 2'd0;
            resp_idx <= 5'd0;
            resp_len <= 5'd0;
            busy <= 1'b0;
            gpio_led <= 5'b01010;
            spi_start <= 0; sdram_start <= 0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (uart_rx_valid) begin
                        cmd_reg <= uart_rx_data;
                        state <= S_DISPATCH;
                    end
                end
                S_DISPATCH: begin
                    case (cmd_reg)
                        CMD_VERSION: begin
                            resp[0] <= CMD_VERSION;
                            resp[1] <= PHASE;
                            resp[2] <= VER;
                            resp_len <= 5'd3;
                        end
                        CMD_PING: begin
                            resp[0] <= CMD_PING;
                            resp[1] <= ACK;
                            resp_len <= 5'd2;
                        end
                        default: begin
                            resp[0] <= cmd_reg;
                            resp[1] <= 8'hEE;
                            resp_len <= 5'd2;
                        end
                    endcase
                    resp_idx <= 5'd0;
                    state <= S_TX_RESP;
                end
                S_TX_RESP: begin
                    if (resp_idx < resp_len) begin
                        tx_fifo[tx_wr] <= resp[resp_idx];
                        tx_wr <= tx_wr + 2'd1;
                        resp_idx <= resp_idx + 5'd1;
                    end else begin
                        state <= S_IDLE;
                    end
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
