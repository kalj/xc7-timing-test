// Self-checking timing test, version 3: reports to the PC over the UART
//
// The same computation as timing_test2.v: an MMCM turns the 12 MHz board
// clock into a fast clock, the design runs 2^20 cycles of an add-rotate-xor
// (ARX) computation, ROUNDS rounds per cycle, checks the result in one more
// cycle, and starts again, forever.
//
// New in version 3: once a second, logic on the 12 MHz clock sends a line
// over the UART (FTDI channel B, 115200 baud, 8N1):
//
//   Sssss LlVv Rrrrrrrrr Fffffffff Ccccccc\r\n        (all numbers hex)
//
//   ssss     line number since configuration
//   l        MMCM locked
//   v        1 if the fast clock answered the last snapshot request
//   rrrrrrrr runs finished since reset
//   ffffffff runs whose result was wrong
//   cccccc   fast-clock cycles / 256, free running (wraps every 2^32 cycles)
//
// The snapshot is taken once per 12,000,000 cycles of the 12 MHz clock, so
// the difference of consecutive C values times 256 is the fast clock in Hz.
// The fast-clock counters change at most once every 256 cycles (C) or once
// per run (R, F), so their adders have many cycles to settle: they keep
// counting correctly even when the ARX path fails timing.
//
// The LEDs work as in version 2: green = all runs correct, red = some run
// wrong, led[0] = locked, led[1] toggles every 256 runs, btn[0] = restart.

`include "params.vh"

module timing_test3 (
    input  wire       clk,      // 12 MHz
    input  wire [1:0] btn,
    output wire [1:0] led,
    output wire       led0_r,   // active low
    output wire       led0_g,   // active low
    output wire       led0_b,   // active low
    output wire       uart_tx
);

    // ---- Clocking: 12 MHz -> BUFG -> clk_slow -> MMCM -> BUFG -> clk_fast ----
    wire clk_slow;
    BUFG bufg_slow (.I(clk), .O(clk_slow));

    wire clk_fb, clk_fast_unbuf, clk_fast, locked;

    MMCME2_BASE #(
        .CLKIN1_PERIOD    (83.333),
        .DIVCLK_DIVIDE    (1),
        .CLKFBOUT_MULT_F  (`MMCM_MULT),
        .CLKOUT0_DIVIDE_F (`MMCM_DIV)
    ) mmcm (
        .CLKIN1   (clk_slow),
        .CLKFBIN  (clk_fb),
        .CLKFBOUT (clk_fb),
        .CLKOUT0  (clk_fast_unbuf),
        .LOCKED   (locked),
        .RST      (1'b0),
        .PWRDWN   (1'b0)
    );

`ifdef CASCADE
    // Two MMCMs in series, for clocks a single MMCM can't make from 12 MHz:
    // mmcm makes 100 MHz, mmcm2 makes the fast clock from that.
    wire clk_mid, clk_fb2, clk_fast2_unbuf, locked2;
    BUFG bufg_mid (.I(clk_fast_unbuf), .O(clk_mid));
    MMCME2_BASE #(
        .CLKIN1_PERIOD    (10.0),
        .DIVCLK_DIVIDE    (1),
        .CLKFBOUT_MULT_F  (`MMCM2_MULT),
        .CLKOUT0_DIVIDE_F (`MMCM2_DIV)
    ) mmcm2 (
        .CLKIN1   (clk_mid),
        .CLKFBIN  (clk_fb2),
        .CLKFBOUT (clk_fb2),
        .CLKOUT0  (clk_fast2_unbuf),
        .LOCKED   (locked2),
        .RST      (~locked),
        .PWRDWN   (1'b0)
    );
    BUFG bufg_fast (.I(clk_fast2_unbuf), .O(clk_fast));
    wire locked_all = locked & locked2;
`else
    BUFG bufg_fast (.I(clk_fast_unbuf), .O(clk_fast));
    wire locked_all = locked;
`endif

    // ---- Reset: hold until MMCM locks; btn[0] restarts ----
    reg [2:0] locked_sync = 3'b000;
    reg [2:0] btn_sync    = 3'b000;
    always @(posedge clk_fast) begin
        locked_sync <= {locked_sync[1:0], locked_all};
        btn_sync    <= {btn_sync[1:0], btn[0]};
    end
    wire rst = ~locked_sync[2] | btn_sync[2];

    // ---- The computation (as in version 2) ----
    function [63:0] arx(input [31:0] a_in, input [31:0] b_in);
        integer i;
        reg [31:0] ta, tb;
        begin
            ta = a_in;
            tb = b_in;
            for (i = 0; i < `ROUNDS; i = i + 1) begin
                ta = ta + tb;
                tb = {tb[24:0], tb[31:25]} ^ ta;   // rotate left by 7, then xor
            end
            arx = {ta, tb};
        end
    endfunction

    reg [31:0]            a, b;
    reg [`LOG2_ITERS-1:0] n;
    reg                   check;      // this cycle compares the result
    reg                   any_done;   // at least one run has finished
    reg                   fail;       // sticky: some run was wrong
    reg [31:0]            runs;       // runs finished since reset
    reg [31:0]            fail_runs;  // runs that were wrong

    always @(posedge clk_fast) begin
        if (rst) begin
            a         <= `SEED_A;
            b         <= `SEED_B;
            n         <= 0;
            check     <= 1'b0;
            any_done  <= 1'b0;
            fail      <= 1'b0;
            runs      <= 32'd0;
            fail_runs <= 32'd0;
        end else if (check) begin
            if ((a != `EXPECTED_A) || (b != `EXPECTED_B)) begin
                fail      <= 1'b1;
                fail_runs <= fail_runs + 1'b1;
            end
            any_done <= 1'b1;
            runs     <= runs + 1'b1;
            a        <= `SEED_A;          // start the next run
            b        <= `SEED_B;
            n        <= 0;
            check    <= 1'b0;
        end else begin
            {a, b} <= arx(a, b);
            n      <= n + 1'b1;
            if (&n)                       // last of 2^LOG2_ITERS iterations
                check <= 1'b1;
        end
    end

    // ---- Fast-clock cycle counter: fc_hi counts every 256 cycles ----
    reg [7:0]  fc_lo   = 8'd0;
    reg        fc_wrap = 1'b0;
    reg [23:0] fc_hi   = 24'd0;
    always @(posedge clk_fast) begin
        fc_lo   <= fc_lo + 1'b1;
        fc_wrap <= &fc_lo;
        if (fc_wrap)
            fc_hi <= fc_hi + 1'b1;
    end

    // ---- Snapshot handshake: the 12 MHz side toggles req, the fast side
    //      copies its counters into h_* and answers by setting ack = req ----
    reg        req = 1'b0;                // clk_slow
    reg [1:0]  req_s = 2'b00;             // clk_fast
    reg        ack = 1'b0;                // clk_fast
    reg [23:0] h_fc   = 24'd0;
    reg [31:0] h_runs = 32'd0;
    reg [31:0] h_fail = 32'd0;
    always @(posedge clk_fast) begin
        req_s <= {req_s[0], req};
        if (req_s[1] != ack) begin
            h_fc   <= fc_hi;
            h_runs <= runs;
            h_fail <= fail_runs;
            ack    <= req_s[1];
        end
    end

    // ---- 12 MHz side: once a second, take the last snapshot and send it ----
    localparam TICKS    = 24'd12_000_000;  // one second of the 12 MHz clock
    localparam BAUD_DIV = 7'd104;          // 12 MHz / 104 = 115385 baud (+0.16 %)
    localparam LINE_LEN = 6'd40;

    reg [23:0] sec_cnt  = 24'd0;
    reg [2:0]  ack_s    = 3'b000;
    reg [1:0]  lock_s   = 2'b00;
    reg [15:0] seq      = 16'd0;
    reg        s_locked = 1'b0;
    reg        s_valid  = 1'b0;
    reg [23:0] s_fc     = 24'd0;
    reg [31:0] s_runs   = 32'd0;
    reg [31:0] s_fail   = 32'd0;

    reg        sending  = 1'b0;
    reg [5:0]  idx      = 6'd0;
    reg [3:0]  nbits    = 4'd0;           // baud periods left in this character
    reg [6:0]  baud_cnt = 7'd0;
    reg [8:0]  sh       = 9'h1FF;
    reg        txd      = 1'b1;
    reg [7:0]  ch;

    function [7:0] hex(input [3:0] v);
        hex = (v < 4'd10) ? (8'h30 + v) : (8'h37 + v);   // '0'-'9', 'A'-'F'
    endfunction

    always @* begin
        case (idx)
            6'd0:  ch = "S";
            6'd1:  ch = hex(seq[15:12]);
            6'd2:  ch = hex(seq[11:8]);
            6'd3:  ch = hex(seq[7:4]);
            6'd4:  ch = hex(seq[3:0]);
            6'd5:  ch = " ";
            6'd6:  ch = "L";
            6'd7:  ch = s_locked ? "1" : "0";
            6'd8:  ch = "V";
            6'd9:  ch = s_valid ? "1" : "0";
            6'd10: ch = " ";
            6'd11: ch = "R";
            6'd12: ch = hex(s_runs[31:28]);
            6'd13: ch = hex(s_runs[27:24]);
            6'd14: ch = hex(s_runs[23:20]);
            6'd15: ch = hex(s_runs[19:16]);
            6'd16: ch = hex(s_runs[15:12]);
            6'd17: ch = hex(s_runs[11:8]);
            6'd18: ch = hex(s_runs[7:4]);
            6'd19: ch = hex(s_runs[3:0]);
            6'd20: ch = " ";
            6'd21: ch = "F";
            6'd22: ch = hex(s_fail[31:28]);
            6'd23: ch = hex(s_fail[27:24]);
            6'd24: ch = hex(s_fail[23:20]);
            6'd25: ch = hex(s_fail[19:16]);
            6'd26: ch = hex(s_fail[15:12]);
            6'd27: ch = hex(s_fail[11:8]);
            6'd28: ch = hex(s_fail[7:4]);
            6'd29: ch = hex(s_fail[3:0]);
            6'd30: ch = " ";
            6'd31: ch = "C";
            6'd32: ch = hex(s_fc[23:20]);
            6'd33: ch = hex(s_fc[19:16]);
            6'd34: ch = hex(s_fc[15:12]);
            6'd35: ch = hex(s_fc[11:8]);
            6'd36: ch = hex(s_fc[7:4]);
            6'd37: ch = hex(s_fc[3:0]);
            6'd38: ch = 8'h0D;
            default: ch = 8'h0A;
        endcase
    end

    always @(posedge clk_slow) begin
        ack_s  <= {ack_s[1:0], ack};
        lock_s <= {lock_s[0], locked_all};

        if (sec_cnt == TICKS - 1'b1) begin
            sec_cnt  <= 24'd0;
            seq      <= seq + 1'b1;
            s_locked <= lock_s[1];
            s_valid  <= (ack_s[2] == req);
            if (ack_s[2] == req) begin
                // The last request was answered: report it, ask again.
                s_fc   <= h_fc;
                s_runs <= h_runs;
                s_fail <= h_fail;
                req    <= ~req;
            end
            sending <= 1'b1;
            idx     <= 6'd0;
        end else begin
            sec_cnt <= sec_cnt + 1'b1;
        end

        // UART transmitter: start bit, 8 data bits LSB first, stop bit
        if (nbits == 4'd0) begin
            txd <= 1'b1;
            if (sending && sec_cnt != TICKS - 1'b1) begin
                txd      <= 1'b0;
                sh       <= {1'b1, ch};
                nbits    <= 4'd10;
                baud_cnt <= 7'd0;
                if (idx == LINE_LEN - 1'b1)
                    sending <= 1'b0;
                else
                    idx <= idx + 1'b1;
            end
        end else if (baud_cnt == BAUD_DIV - 1'b1) begin
            baud_cnt <= 7'd0;
            nbits    <= nbits - 1'b1;
            txd      <= sh[0];
            sh       <= {1'b1, sh[8:1]};
        end else begin
            baud_cnt <= baud_cnt + 1'b1;
        end
    end

    assign uart_tx = txd;

    // ---- Display (as in version 2) ----
    reg [11:0] pwm = 12'd0;
    always @(posedge clk_fast)
        pwm <= pwm + 1'b1;
    wire bright = (pwm[11:9] == 3'd0);

    reg g_on = 1'b0, r_on = 1'b0;
    always @(posedge clk_fast) begin
        g_on <= bright & any_done & ~fail;
        r_on <= bright & fail;
    end

    assign led    = {runs[8], locked_sync[2]};
    assign led0_g = ~g_on;
    assign led0_r = ~r_on;
    assign led0_b = 1'b1;

endmodule
