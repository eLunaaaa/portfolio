// 파일 설명: tb_pool_layer 연산을 알려진 입력·기대값으로 검사하는 시뮬레이션 테스트입니다.
// 입력: 테스트에서 생성한 클록·리셋·데이터. 출력: PASS 또는 $fatal 종료.
// 구조: 초기화→시작 신호→완료 대기→출력 비교.
//============================================================================
// Testbench: tb_pool_layer
// Description: Test max pooling layer
//============================================================================

`timescale 1ns/1ps

module tb_pool_layer;

    parameter CHANNELS = 2;
    parameter IN_HEIGHT = 8;
    parameter IN_WIDTH = 8;
    parameter POOL_SIZE = 2;
    parameter STRIDE = 2;
    parameter DATA_WIDTH = 8;

    localparam OUT_HEIGHT = (IN_HEIGHT - POOL_SIZE) / STRIDE + 1;
    localparam OUT_WIDTH = (IN_WIDTH - POOL_SIZE) / STRIDE + 1;

    // Signals - CORRECTED [MSB:0]
    reg clk;
    reg rst_n;
    reg start;
    reg [DATA_WIDTH-1:0] in_data [CHANNELS-1:0][IN_HEIGHT-1:0][IN_WIDTH-1:0];
    wire [DATA_WIDTH-1:0] out_data [CHANNELS-1:0][OUT_HEIGHT-1:0][OUT_WIDTH-1:0];
    wire done;

    // DUT
    pool_layer #(
        .CHANNELS(CHANNELS),
        .IN_HEIGHT(IN_HEIGHT),
        .IN_WIDTH(IN_WIDTH),
        .POOL_SIZE(POOL_SIZE),
        .STRIDE(STRIDE),
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .start(start),
        .in_data(in_data),
        .out_data(out_data),
        .done(done)
    );

    // Clock
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // Test
    integer ch, y, x;
    integer errors;

    initial begin
        $display("\n=== Max Pooling Layer Test ===\n");

        errors = 0;
        rst_n = 0;
        start = 0;

        // Initialize input with pattern
        for (ch = 0; ch < CHANNELS; ch = ch + 1)
            for (y = 0; y < IN_HEIGHT; y = y + 1)
                for (x = 0; x < IN_WIDTH; x = x + 1)
                    in_data[ch][y][x] = (y * IN_WIDTH + x) & 8'hFF;

        #100 rst_n = 1;
        #50;

        $display("Test 1: Check initial state");
        if (done !== 0) begin
            $display("  ✗ FAIL: done should be 0");
            if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
        end
        $display("  ✓ PASS: Initial state correct");

        $display("\nTest 2: Known pattern - check maximum");

        // IMPORTANT: Fill entire input array first with known values
        for (ch = 0; ch < CHANNELS; ch = ch + 1)
            for (y = 0; y < IN_HEIGHT; y = y + 1)
                for (x = 0; x < IN_WIDTH; x = x + 1)
                    in_data[ch][y][x] = 8'h00;

        // Set known 2x2 blocks for channel 0
        // Block at [0:1][0:1] with max at [1][1]
        in_data[0][0][0] = 8'h10;
        in_data[0][0][1] = 8'h20;
        in_data[0][1][0] = 8'h30;
        in_data[0][1][1] = 8'h40;  // Maximum = 0x40 (64)

        // Block at [0:1][2:3] with max at [0][2]
        in_data[0][0][2] = 8'h50;  // Maximum = 0x50 (80)
        in_data[0][0][3] = 8'h30;
        in_data[0][1][2] = 8'h20;
        in_data[0][1][3] = 8'h10;

        // Block at [2:3][0:1] with max at [2][1]
        in_data[0][2][0] = 8'h15;
        in_data[0][2][1] = 8'h35;  // Maximum = 0x35 (53)
        in_data[0][3][0] = 8'h25;
        in_data[0][3][1] = 8'h05;

        // Block at [2:3][2:3] with max at [3][3]
        in_data[0][2][2] = 8'h11;
        in_data[0][2][3] = 8'h22;
        in_data[0][3][2] = 8'h33;
        in_data[0][3][3] = 8'h44;  // Maximum = 0x44 (68)

        $display("  Input windows set:");
        $display("    Window[0][0]: [10,20,30,40] -> expect max = 40");
        $display("    Window[0][1]: [50,30,20,10] -> expect max = 50");
        $display("    Window[1][0]: [15,35,25,05] -> expect max = 35");
        $display("    Window[1][1]: [11,22,33,44] -> expect max = 44");

        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        $display("  Waiting for done...");
        wait(done == 1);
        @(posedge clk);

        $display("  Checking results...");

        // FIRST: Check for X/Z values (MUST be done before value comparison!)
        if (^out_data[0][0][0] === 1'bx || out_data[0][0][0] === 8'bz) begin
            $display("  ✗ FAIL: out[0][0][0] has X or Z bits: %b", out_data[0][0][0]);
            errors = errors + 1;
        end else if (out_data[0][0][0] !== 8'h40) begin
            $display("  ✗ FAIL: out[0][0][0] = %h, expected 40", out_data[0][0][0]);
            $display("         Input window was: [%h,%h,%h,%h]",
                     in_data[0][0][0], in_data[0][0][1],
                     in_data[0][1][0], in_data[0][1][1]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[0][0][0] = 40 (correct max)");
        end

        // Check second pooling window [0][1]
        if (^out_data[0][0][1] === 1'bx || out_data[0][0][1] === 8'bz) begin
            $display("  ✗ FAIL: out[0][0][1] has X or Z bits: %b", out_data[0][0][1]);
            errors = errors + 1;
        end else if (out_data[0][0][1] !== 8'h50) begin
            $display("  ✗ FAIL: out[0][0][1] = %h, expected 50", out_data[0][0][1]);
            $display("         Input window was: [%h,%h,%h,%h]",
                     in_data[0][0][2], in_data[0][0][3],
                     in_data[0][1][2], in_data[0][1][3]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[0][0][1] = 50 (correct max)");
        end

        // Check third pooling window [1][0]
        if (^out_data[0][1][0] === 1'bx || out_data[0][1][0] === 8'bz) begin
            $display("  ✗ FAIL: out[0][1][0] has X or Z bits: %b", out_data[0][1][0]);
            errors = errors + 1;
        end else if (out_data[0][1][0] !== 8'h35) begin
            $display("  ✗ FAIL: out[0][1][0] = %h, expected 35", out_data[0][1][0]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[0][1][0] = 35 (correct max)");
        end

        // Check fourth pooling window [1][1]
        if (^out_data[0][1][1] === 1'bx || out_data[0][1][1] === 8'bz) begin
            $display("  ✗ FAIL: out[0][1][1] has X or Z bits: %b", out_data[0][1][1]);
            errors = errors + 1;
        end else if (out_data[0][1][1] !== 8'h44) begin
            $display("  ✗ FAIL: out[0][1][1] = %h, expected 44", out_data[0][1][1]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[0][1][1] = 44 (correct max)");
        end

        $display("\nTest 3: Check all outputs valid");
        for (ch = 0; ch < CHANNELS; ch = ch + 1)
            for (y = 0; y < OUT_HEIGHT; y = y + 1)
                for (x = 0; x < OUT_WIDTH; x = x + 1)
                    if (^out_data[ch][y][x] === 1'bx) begin
                        $display("  ✗ FAIL: out[%0d][%0d][%0d] is X", ch, y, x);
                        errors = errors + 1;
                    end

        if (errors == 0)
            $display("  ✓ PASS: All outputs valid");

        $display("\nTest 4: Output dimensions");
        $display("  Expected: %0dx%0d", OUT_HEIGHT, OUT_WIDTH);
        $display("  ✓ PASS: Dimensions correct");

        $display("\n========================================");
        if (errors == 0) begin
            $display("✓✓✓ ALL TESTS PASSED ✓✓✓");
        end else begin
            $display("✗✗✗ %0d TEST(S) FAILED ✗✗✗", errors);
        end
        $display("========================================\n");

        if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
    end

    initial begin
        $dumpfile("tb_pool_layer.vcd");
        $dumpvars(0, tb_pool_layer);
    end

endmodule
