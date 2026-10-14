// 파일 설명: tb_fc_layer 연산을 알려진 입력·기대값으로 검사하는 시뮬레이션 테스트입니다.
// 입력: 테스트에서 생성한 클록·리셋·데이터. 출력: PASS 또는 $fatal 종료.
// 구조: 초기화→시작 신호→완료 대기→출력 비교.
//============================================================================
// Testbench: tb_fc_layer
// Description: Test fully connected layer
// Array order: [MSB:0]
//============================================================================

`timescale 1ns/1ps

module tb_fc_layer;

    parameter INPUT_SIZE = 16;
    parameter OUTPUT_SIZE = 4;
    parameter DATA_WIDTH = 8;

    // Signals - CORRECTED [MSB:0]
    reg clk;
    reg rst_n;
    reg start;
    reg [DATA_WIDTH-1:0] in_data [INPUT_SIZE-1:0];
    reg [DATA_WIDTH-1:0] weights [OUTPUT_SIZE-1:0][INPUT_SIZE-1:0];
    reg [DATA_WIDTH-1:0] bias [OUTPUT_SIZE-1:0];
    wire [DATA_WIDTH-1:0] out_data [OUTPUT_SIZE-1:0];
    wire done;

    // DUT
    fc_layer #(
        .INPUT_SIZE(INPUT_SIZE),
        .OUTPUT_SIZE(OUTPUT_SIZE),
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .start(start),
        .in_data(in_data),
        .weights(weights),
        .bias(bias),
        .out_data(out_data),
        .done(done)
    );

    // Clock
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // Test
    integer i, o;
    integer errors;

    initial begin
        $display("\n=== Fully Connected Layer Test ===\n");

        errors = 0;
        rst_n = 0;
        start = 0;

        // Initialize inputs
        for (i = 0; i < INPUT_SIZE; i = i + 1)
            in_data[i] = 8'h00;

        // Initialize weights and bias
        for (o = 0; o < OUTPUT_SIZE; o = o + 1) begin
            bias[o] = 8'h00;
            for (i = 0; i < INPUT_SIZE; i = i + 1)
                weights[o][i] = 8'h00;
        end

        #100 rst_n = 1;
        #50;

        $display("Test 1: Check initial state");
        if (done !== 0) begin
            $display("  ✗ FAIL: done should be 0");
            if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
        end
        $display("  ✓ PASS: Initial state correct");

        $display("\nTest 2: Simple known computation");
        $display("  Setting up: output[i] = sum(input[j] * weight[i][j]) + bias[i]");

        // Clear everything first
        for (i = 0; i < INPUT_SIZE; i = i + 1)
            in_data[i] = 8'h00;

        for (o = 0; o < OUTPUT_SIZE; o = o + 1) begin
            bias[o] = 8'h00;
            for (i = 0; i < INPUT_SIZE; i = i + 1)
                weights[o][i] = 8'h00;
        end

        // Test 1: Simple case - one input, one weight
        // Output[0] = input[0] * weight[0][0] + bias[0]
        //          = 16 * 16 / 16 + 0 = 16
        in_data[0] = 8'h10;        // 16
        weights[0][0] = 8'h10;     // 16
        bias[0] = 8'h00;           // 0
        // Expected: 16*16 = 256, shifted right by 4 = 16

        // Output[1] = input[1] * weight[1][1] + bias[1]
        //          = 32 * 8 / 16 + 0 = 16
        in_data[1] = 8'h20;        // 32
        weights[1][1] = 8'h08;     // 8
        bias[1] = 8'h00;
        // Expected: 32*8 = 256, shifted right by 4 = 16

        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        $display("  Waiting for done...");
        wait(done == 1);
        @(posedge clk);

        $display("  Checking results...");
        $display("    out[0] = %h (expected: 10 = 16*16>>4)", out_data[0]);
        $display("    out[1] = %h (expected: 10 = 32*8>>4)", out_data[1]);

        // Check for X/Z FIRST before value comparison
        if (^out_data[0] === 1'bx || out_data[0] === 8'bz) begin
            $display("  ✗ FAIL: out[0] has X or Z bits: %b", out_data[0]);
            errors = errors + 1;
        end else if (out_data[0] < 8'h0E || out_data[0] > 8'h12) begin
            $display("  ✗ FAIL: out[0] = %h, expected ~10", out_data[0]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[0] in expected range");
        end

        if (^out_data[1] === 1'bx || out_data[1] === 8'bz) begin
            $display("  ✗ FAIL: out[1] has X or Z bits: %b", out_data[1]);
            errors = errors + 1;
        end else if (out_data[1] < 8'h0E || out_data[1] > 8'h12) begin
            $display("  ✗ FAIL: out[1] = %h, expected ~10", out_data[1]);
            errors = errors + 1;
        end else begin
            $display("  ✓ PASS: out[1] in expected range");
        end

        $display("\nTest 3: All zeros input (should give bias only)");

        for (i = 0; i < INPUT_SIZE; i = i + 1)
            in_data[i] = 8'h00;

        // Set different bias values
        bias[0] = 8'h10;  // 16
        bias[1] = 8'h20;  // 32
        bias[2] = 8'h30;  // 48
        bias[3] = 8'h40;  // 64

        // Weights don't matter since input is zero
        for (o = 0; o < OUTPUT_SIZE; o = o + 1)
            for (i = 0; i < INPUT_SIZE; i = i + 1)
                weights[o][i] = 8'h00;

        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        wait(done == 1);
        @(posedge clk);

        $display("  Expected outputs should match bias:");
        $display("    out[0] should be ~10, got %h", out_data[0]);
        $display("    out[1] should be ~20, got %h", out_data[1]);
        $display("    out[2] should be ~30, got %h", out_data[2]);
        $display("    out[3] should be ~40, got %h", out_data[3]);

        // Check for X/Z FIRST
        if (^out_data[0] === 1'bx || out_data[0] === 8'bz) begin
            $display("  ✗ FAIL: out[0] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[0] < 8'h0E || out_data[0] > 8'h12) begin
            $display("  ✗ FAIL: out[0] should be ~10");
            errors = errors + 1;
        end

        if (^out_data[1] === 1'bx || out_data[1] === 8'bz) begin
            $display("  ✗ FAIL: out[1] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[1] < 8'h1E || out_data[1] > 8'h22) begin
            $display("  ✗ FAIL: out[1] should be ~20");
            errors = errors + 1;
        end

        if (^out_data[2] === 1'bx || out_data[2] === 8'bz) begin
            $display("  ✗ FAIL: out[2] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[2] < 8'h2E || out_data[2] > 8'h32) begin
            $display("  ✗ FAIL: out[2] should be ~30");
            errors = errors + 1;
        end

        if (^out_data[3] === 1'bx || out_data[3] === 8'bz) begin
            $display("  ✗ FAIL: out[3] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[3] < 8'h3E || out_data[3] > 8'h42) begin
            $display("  ✗ FAIL: out[3] should be ~40");
            errors = errors + 1;
        end

        if (errors == 0)
            $display("  ✓ PASS: Bias-only computation correct");

        $display("\nTest 4: Multiple inputs contribute to output");

        // Test accumulation: multiple inputs should add up
        for (i = 0; i < INPUT_SIZE; i = i + 1)
            in_data[i] = 8'h08;  // All inputs = 8

        // Output[0] gets contribution from first 4 inputs
        for (i = 0; i < 4; i = i + 1)
            weights[0][i] = 8'h04;  // weight = 4
        for (i = 4; i < INPUT_SIZE; i = i + 1)
            weights[0][i] = 8'h00;
        bias[0] = 8'h00;

        // Expected: 4 * (8*4/16) = 4 * 2 = 8

        // Output[1] gets contribution from all inputs
        for (i = 0; i < INPUT_SIZE; i = i + 1)
            weights[1][i] = 8'h02;  // weight = 2
        bias[1] = 8'h00;

        // Expected: 16 * (8*2/16) = 16 * 1 = 16

        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        wait(done == 1);
        @(posedge clk);

        $display("  Results with accumulation:");
        $display("  out[0] = %h (expected: ~08 from 4 inputs)", out_data[0]);
        $display("  out[1] = %h (expected: ~10 from 16 inputs)", out_data[1]);

        // Check for X/Z FIRST
        if (^out_data[0] === 1'bx || out_data[0] === 8'bz) begin
            $display("    ✗ FAIL: out[0] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[0] < 8'h06 || out_data[0] > 8'h0A) begin
            $display("    ✗ FAIL: out[0] should be ~08");
            errors = errors + 1;
        end else begin
            $display("    ✓ PASS: out[0] correct");
        end

        if (^out_data[1] === 1'bx || out_data[1] === 8'bz) begin
            $display("    ✗ FAIL: out[1] has X or Z bits");
            errors = errors + 1;
        end else if (out_data[1] < 8'h0E || out_data[1] > 8'h12) begin
            $display("    ✗ FAIL: out[1] should be ~10");
            errors = errors + 1;
        end else begin
            $display("    ✓ PASS: out[1] correct");
        end

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
        $dumpfile("tb_fc_layer.vcd");
        $dumpvars(0, tb_fc_layer);
    end

endmodule
