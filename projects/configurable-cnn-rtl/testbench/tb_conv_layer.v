// 파일 설명: tb_conv_layer 연산을 알려진 입력·기대값으로 검사하는 시뮬레이션 테스트입니다.
// 입력: 테스트에서 생성한 클록·리셋·데이터. 출력: PASS 또는 $fatal 종료.
// 구조: 초기화→시작 신호→완료 대기→출력 비교.
//============================================================================
// Testbench: tb_conv_layer
// Description: Unit test for convolution layer module
//============================================================================

`timescale 1ns/1ps

module tb_conv_layer;

    parameter DATA_WIDTH = 8;
    parameter KERNEL_SIZE = 3;
    parameter STRIDE = 1;
    parameter IN_CHANNELS = 1;
    parameter OUT_CHANNELS = 2;
    parameter IN_WIDTH = 5;
    parameter IN_HEIGHT = 5;

    // Calculated output dimensions
    localparam OUT_HEIGHT = (IN_HEIGHT - KERNEL_SIZE) / STRIDE + 1;
    localparam OUT_WIDTH = (IN_WIDTH - KERNEL_SIZE) / STRIDE + 1;

    // Signals
    reg clk;
    reg rst_n;
    reg start;
    reg [DATA_WIDTH-1:0] in_feature_map [IN_CHANNELS-1:0][IN_HEIGHT-1:0][IN_WIDTH-1:0];
    reg [DATA_WIDTH-1:0] weights [OUT_CHANNELS-1:0][IN_CHANNELS-1:0][KERNEL_SIZE-1:0][KERNEL_SIZE-1:0];
    reg [DATA_WIDTH-1:0] bias [OUT_CHANNELS-1:0];
    wire [DATA_WIDTH-1:0] out_feature_map [OUT_CHANNELS-1:0][OUT_HEIGHT-1:0][OUT_WIDTH-1:0];
    wire done;

    // DUT instantiation
    conv_layer #(
        .KERNEL_SIZE(KERNEL_SIZE),
        .STRIDE(STRIDE),
        .IN_CHANNELS(IN_CHANNELS),
        .OUT_CHANNELS(OUT_CHANNELS),
        .IN_WIDTH(IN_WIDTH),
        .IN_HEIGHT(IN_HEIGHT),
        .DATA_WIDTH(DATA_WIDTH)
    ) dut (
        .clk(clk),
        .rst_n(rst_n),
        .start(start),
        .in_feature_map(in_feature_map),
        .weights(weights),
        .bias(bias),
        .out_feature_map(out_feature_map),
        .done(done)
    );

    // Clock generation
    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    // Test stimulus
    integer i, j, k, och, ich, ky, kx;
    integer errors;
	integer total_terms;

	reg signed [15:0] expected_full;
	reg signed [7:0] expected_clipped;

    initial begin
        $dumpfile("conv_layer.vcd");
        $dumpvars(0, tb_conv_layer);

        // Initialize
        rst_n = 0;
        start = 0;
        errors = 0;

        // Reset for a few cycles
        repeat(5) @(posedge clk);
        rst_n = 1;
        @(posedge clk);

        $display("=== Convolution Layer Test ===");
        $display("Config: %0dx%0d kernel, stride=%0d, %0d->%0d channels",
                 KERNEL_SIZE, KERNEL_SIZE, STRIDE, IN_CHANNELS, OUT_CHANNELS);
        $display("Input: %0dx%0d, Output: %0dx%0d", IN_HEIGHT, IN_WIDTH, OUT_HEIGHT, OUT_WIDTH);

        // Test Case 1: Simple identity-like convolution
        $display("\nTest 1: Simple convolution");

        // Initialize input (simple pattern)
        for (ich = 0; ich < IN_CHANNELS; ich = ich + 1) begin
            for (i = 0; i < IN_HEIGHT; i = i + 1) begin
                for (j = 0; j < IN_WIDTH; j = j + 1) begin
                    // Q3.4 format: 1.0 = 8'h10
                    in_feature_map[ich][i][j] = (i + j) << 4;  // Simple gradient pattern
                end
            end
        end

        // Initialize weights (simple 3x3 kernel)
        for (och = 0; och < OUT_CHANNELS; och = och + 1) begin
            for (ich = 0; ich < IN_CHANNELS; ich = ich + 1) begin
                for (ky = 0; ky < KERNEL_SIZE; ky = ky + 1) begin
                    for (kx = 0; kx < KERNEL_SIZE; kx = kx + 1) begin
                        if (ky == 1 && kx == 1)
                            weights[och][ich][ky][kx] = 8'h10;  // Center = 1.0
                        else
                            weights[och][ich][ky][kx] = 8'h00;  // Others = 0
                    end
                end
            end
            bias[och] = 8'h20;
        end

        // Start convolution
        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        // Wait for completion
        wait(done);
        @(posedge clk);

        $display("Convolution completed!");

        // Check outputs
        $display("\nOutput feature map:");
        for (och = 0; och < OUT_CHANNELS; och = och + 1) begin
            $display("Channel %0d:", och);
            for (i = 0; i < OUT_HEIGHT; i = i + 1) begin
                $write("  Row %0d: ", i);
                for (j = 0; j < OUT_WIDTH; j = j + 1) begin
					// Expected value = (oy + ox + 2) << 4 + bias
					expected_full = ((i + j + 2) << 4) + 8'h20;
					expected_clipped = expected_full > 127 ? 8'h7F : (expected_full < -128 ? 8'h80 : expected_full[7:0]);
					if (out_feature_map[och][i][j] !== expected_clipped) begin
						$display("❌ MISMATCH at och=%0d, y=%0d, x=%0d | got=%0h expected_clipped=%0h",
								 och, i, j, out_feature_map[och][i][j], expected_clipped);
						errors = errors + 1;
					end
					else
						$write("%02h ", out_feature_map[och][i][j]);

                end
                $write("\n");
            end
        end

        // Test Case 2: Check for overflow handling
        $display("\nTest 2: Overflow handling");

        // Set large values
        for (ich = 0; ich < IN_CHANNELS; ich = ich + 1) begin
            for (i = 0; i < IN_HEIGHT; i = i + 1) begin
                for (j = 0; j < IN_WIDTH; j = j + 1) begin
                    in_feature_map[ich][i][j] = 8'h7F;  // Max positive
                end
            end
        end

        // All-ones kernel
        for (och = 0; och < OUT_CHANNELS; och = och + 1) begin
            for (ich = 0; ich < IN_CHANNELS; ich = ich + 1) begin
                for (ky = 0; ky < KERNEL_SIZE; ky = ky + 1) begin
                    for (kx = 0; kx < KERNEL_SIZE; kx = kx + 1) begin
                        weights[och][ich][ky][kx] = 8'h10;  // 1.0
                    end
                end
            end
            bias[och] = 8'h00;
        end

        @(posedge clk);
        start = 1;
        @(posedge clk);
        start = 0;

        wait(done);
        @(posedge clk);


		$display("Checking overflow & clipping behavior...");

		// Expected accumulation per output pixel:
		// accumulator_full = IN_CHANNELS * KERNEL_SIZE * KERNEL_SIZE * (7F * 1.0)
		//                   = (#inp_channels) * (kernel_area) * 127
		// Then clipped to range [-128,127]


		total_terms = IN_CHANNELS * KERNEL_SIZE * KERNEL_SIZE;

		for (och = 0; och < OUT_CHANNELS; och = och + 1) begin
			for (i = 0; i < OUT_HEIGHT; i = i + 1) begin
				for (j = 0; j < OUT_WIDTH; j = j + 1) begin

					expected_full = total_terms * 127;

					// Apply clipping (same as design)
					if (expected_full > 127)
						expected_clipped = 8'h7F;
					else if (expected_full < -128)
						expected_clipped = 8'h80;
					else
						expected_clipped = expected_full[7:0];

					if (out_feature_map[och][i][j] !== expected_clipped) begin
						$display("❌ MISMATCH @ Test2: och=%0d y=%0d x=%0d | got=%0h expected=%0h",
								 och, i, j,
								 out_feature_map[och][i][j],
								 expected_clipped);
						errors = errors + 1;
					end
					else
						$write("%02h ", out_feature_map[och][i][j]);
				end
				$write("\n");
			end
		end

        // Summary
        $display("\n=== Test Complete ===");
        if (errors == 0)
            $display("PASS: All tests passed!");
        else
            $display("FAIL: %0d errors detected", errors);

        #100;
        if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
    end

    // Timeout watchdog
    initial begin
        #10000;
        $display("ERROR: Simulation timeout!");
        if (errors != 0) $fatal(1, "연산 검증 실패");
        $finish;
    end

endmodule
