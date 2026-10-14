// 파일 설명: tb_relu_layer 연산을 알려진 입력·기대값으로 검사하는 시뮬레이션 테스트입니다.
// 입력: 테스트에서 생성한 클록·리셋·데이터. 출력: PASS 또는 $fatal 종료.
// 구조: 초기화→시작 신호→완료 대기→출력 비교.
//============================================================================
// Testbench: tb_relu_layer
// Description: Unit test for ReLU activation layer
//============================================================================

`timescale 1ns/1ps

module tb_relu_layer;

    parameter DATA_WIDTH = 8;
    parameter CHANNELS = 2;
    parameter HEIGHT = 4;
    parameter WIDTH = 4;

    // Signals
    reg clk;
    reg rst_n;
    reg [DATA_WIDTH-1:0] in_data [CHANNELS-1:0][HEIGHT-1:0][WIDTH-1:0];
    wire [DATA_WIDTH-1:0] out_data [CHANNELS-1:0][HEIGHT-1:0][WIDTH-1:0];

    // DUT instantiation
    relu_layer #(
        .CHANNELS(CHANNELS),
        .HEIGHT(HEIGHT),
        .WIDTH(WIDTH),
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .in_data(in_data),
        .out_data(out_data)
    );

    // Clock generation
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // Test stimulus
    integer ch, y, x, errors;

    initial begin
        $dumpfile("relu_layer.vcd");
        $dumpvars(0, tb_relu_layer);

        // Initialize
        rst_n = 0;
        errors = 0;

        repeat(5) @(posedge clk);
        rst_n = 1;
        @(posedge clk);

        $display("=== ReLU Layer Test ===");
        $display("Config: %0d channels, %0dx%0d feature map", CHANNELS, HEIGHT, WIDTH);

        // Test Case 1: Mixed positive and negative values
        $display("\nTest 1: Mixed positive/negative values");

        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    // Q3.4: positive values in top half, negative in bottom half
                    if (y < HEIGHT/2)
                        in_data[ch][y][x] = ((y * WIDTH + x) << 4);  // Positive
                    else
                        in_data[ch][y][x] = 8'b1000_0000 | ((y * WIDTH + x) << 4);  // Negative
                end
            end
        end

        @(posedge clk);
        @(posedge clk);  // Wait for computation

        // Verify outputs
        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            $display("Channel %0d:", ch);
            for (y = 0; y < HEIGHT; y = y + 1) begin
                $write("  In  Row %0d: ", y);
                for (x = 0; x < WIDTH; x = x + 1) begin
                    $write("%02h ", in_data[ch][y][x]);
                end
                $write("\n");

                $write("  Out Row %0d: ", y);
                for (x = 0; x < WIDTH; x = x + 1) begin
                    $write("%02h ", out_data[ch][y][x]);

                    // Check correctness
                    if (in_data[ch][y][x][DATA_WIDTH-1] == 1'b1) begin
                        // Negative input -> should be 0
                        if (out_data[ch][y][x] != 0) begin
                            $display("\n  ERROR: Negative input not zeroed at [%0d][%0d][%0d]", ch, y, x);
                            errors = errors + 1;
                        end
                    end else begin
                        // Positive input -> should pass through
                        if (out_data[ch][y][x] != in_data[ch][y][x]) begin
                            $display("\n  ERROR: Positive input not passed through at [%0d][%0d][%0d]", ch, y, x);
                            errors = errors + 1;
                        end
                    end
                end
                $write("\n");
            end
        end

        // Test Case 2: All zeros
        $display("\nTest 2: All zeros");
        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    in_data[ch][y][x] = 0;
                end
            end
        end

        @(posedge clk);
        @(posedge clk);

        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    if (out_data[ch][y][x] != 0) begin
                        $display("ERROR: Zero input produced non-zero output at [%0d][%0d][%0d]", ch, y, x);
                        errors = errors + 1;
                    end
                end
            end
        end

        $display("Test 2: %s", (errors == 0) ? "PASS" : "FAIL");

        // Test Case 3: All maximum positive
        $display("\nTest 3: Maximum positive values");
        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    in_data[ch][y][x] = 8'h7F;  // Max positive
                end
            end
        end

        @(posedge clk);
        @(posedge clk);

        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    if (out_data[ch][y][x] != 8'h7F) begin
                        $display("ERROR: Max positive not preserved at [%0d][%0d][%0d]", ch, y, x);
                        errors = errors + 1;
                    end
                end
            end
        end

        $display("Test 3: %s", (errors == 0) ? "PASS" : "FAIL");

        // Test Case 4: All minimum negative
        $display("\nTest 4: Minimum negative values");
        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    in_data[ch][y][x] = 8'h80;  // Most negative
                end
            end
        end

        @(posedge clk);
        @(posedge clk);

        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (y = 0; y < HEIGHT; y = y + 1) begin
                for (x = 0; x < WIDTH; x = x + 1) begin
                    if (out_data[ch][y][x] != 0) begin
                        $display("ERROR: Negative not zeroed at [%0d][%0d][%0d]", ch, y, x);
                        errors = errors + 1;
                    end
                end
            end
        end

        $display("Test 4: %s", (errors == 0) ? "PASS" : "FAIL");

        // Summary
        $display("\n=== Test Complete ===");
        if (errors == 0)
            $display("PASS: All %0d tests passed!", 4);
        else
            $display("FAIL: %0d errors detected", errors);

        #100;
        if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
    end

    // Timeout
    initial begin
        #10000;
        $display("ERROR: Simulation timeout!");
        if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
    end

endmodule
