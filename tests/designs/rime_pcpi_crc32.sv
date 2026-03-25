// CRC32 coprocessor via PicoRV32 PCPI interface.
// Single-cycle CRC32 byte update using custom-1 opcode (0x2B).
//
// Usage from C:
//   unsigned int crc32_hw(unsigned int crc, unsigned int byte) {
//       unsigned int result;
//       asm volatile (".insn r 0x2B, 0, 0, %0, %1, %2"
//                     : "=r"(result) : "r"(crc), "r"(byte));
//       return result;
//   }
//
// Polynomial: 0xEDB88320 (reflected CRC32/ISO-HDLC).
// Computes: crc ^= byte; for 8 bits: if (crc&1) crc=(crc>>1)^poly else crc>>=1;
module rime_pcpi_crc32 (
    input  wire        clk,
    input  wire        resetn,
    input  wire        pcpi_valid,
    input  wire [31:0] pcpi_insn,
    input  wire [31:0] pcpi_rs1,
    input  wire [31:0] pcpi_rs2,
    output reg         pcpi_wr,
    output reg  [31:0] pcpi_rd,
    output wire        pcpi_wait,
    output reg         pcpi_ready
);
    assign pcpi_wait = 1'b0;

    wire is_crc32 = pcpi_valid && (pcpi_insn[6:0] == 7'b0101011);

    // 8 rounds of CRC32 shift-XOR, fully combinational
    wire [31:0] ci = pcpi_rs1 ^ {24'd0, pcpi_rs2[7:0]};
    wire [31:0] c0 = ci[0] ? (ci >> 1) ^ 32'hEDB88320 : (ci >> 1);
    wire [31:0] c1 = c0[0] ? (c0 >> 1) ^ 32'hEDB88320 : (c0 >> 1);
    wire [31:0] c2 = c1[0] ? (c1 >> 1) ^ 32'hEDB88320 : (c1 >> 1);
    wire [31:0] c3 = c2[0] ? (c2 >> 1) ^ 32'hEDB88320 : (c2 >> 1);
    wire [31:0] c4 = c3[0] ? (c3 >> 1) ^ 32'hEDB88320 : (c3 >> 1);
    wire [31:0] c5 = c4[0] ? (c4 >> 1) ^ 32'hEDB88320 : (c4 >> 1);
    wire [31:0] c6 = c5[0] ? (c5 >> 1) ^ 32'hEDB88320 : (c5 >> 1);
    wire [31:0] c7 = c6[0] ? (c6 >> 1) ^ 32'hEDB88320 : (c6 >> 1);

    always @(posedge clk) begin
        pcpi_wr    <= 1'b0;
        pcpi_ready <= 1'b0;
        if (resetn && is_crc32) begin
            pcpi_wr    <= 1'b1;
            pcpi_rd    <= c7;
            pcpi_ready <= 1'b1;
        end
    end
endmodule
