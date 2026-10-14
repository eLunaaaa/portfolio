// 파일 설명: 입력 특징 맵과 커널을 합성곱하여 출력 채널별 특징 맵을 생성합니다.
// 입력: clk, rst_n(0에서 리셋), start, 데이터 배열, 계수와 bias.
// 출력: 변환된 데이터 배열과 done(완료 펄스).
// 구조: 파라미터로 배열 크기를 결정하고 조합 연산과 순차 저장을 분리합니다.
// 수치 형식: signed Q3.4. 곱마다 산술 우측 4비트 이동 후 누적, 최종 signed 범위 포화.
//============================================================================
// Module: conv_layer
// Description: 2D Convolution Layer
//============================================================================

module conv_layer #(
    parameter KERNEL_SIZE = 5,
    parameter STRIDE = 1,
    parameter IN_CHANNELS = 1,
    parameter OUT_CHANNELS = 8,
    parameter IN_WIDTH = 28,
    parameter IN_HEIGHT = 28,
    parameter DATA_WIDTH = 8
)(
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [DATA_WIDTH-1:0] in_feature_map [IN_CHANNELS-1:0][IN_HEIGHT-1:0][IN_WIDTH-1:0],
    input wire [DATA_WIDTH-1:0] weights [OUT_CHANNELS-1:0][IN_CHANNELS-1:0][KERNEL_SIZE-1:0][KERNEL_SIZE-1:0],
    input wire [DATA_WIDTH-1:0] bias [OUT_CHANNELS-1:0],

    output reg [DATA_WIDTH-1:0] out_feature_map [OUT_CHANNELS-1:0][OUT_HEIGHT-1:0][OUT_WIDTH-1:0],
    output reg done
);
    localparam OUT_HEIGHT = (IN_HEIGHT - KERNEL_SIZE) / STRIDE + 1;
    localparam OUT_WIDTH = (IN_WIDTH - KERNEL_SIZE) / STRIDE + 1;
    localparam IDLE = 2'b00;
    localparam COMPUTE = 2'b01;
    localparam FINISH = 2'b10;
    // Sequential state registers
    reg [1:0] state;
    reg [$clog2(OUT_CHANNELS):0] och;
    reg [$clog2(OUT_HEIGHT):0] oy;
    reg [$clog2(OUT_WIDTH):0] ox;
    // Combinational signals (computed in always @(*))
    localparam ACC_WIDTH = 2 * DATA_WIDTH + $clog2(IN_CHANNELS * KERNEL_SIZE * KERNEL_SIZE) + 1;
    localparam signed [ACC_WIDTH-1:0] SAT_MAX = (1 << (DATA_WIDTH-1)) - 1;
    localparam signed [ACC_WIDTH-1:0] SAT_MIN = -(1 << (DATA_WIDTH-1));
    reg signed [ACC_WIDTH-1:0] conv_result;
    reg [DATA_WIDTH-1:0] conv_output;
    // Loop variables
    integer ich, ky, kx;
    reg signed [DATA_WIDTH-1:0] in_val, weight_val;
    reg signed [2*DATA_WIDTH-1:0] product;
    //========================================================================
    // Convolution Computation
    //========================================================================
    // 기능: 현재 위치의 결과 계산. 입력: 데이터·현재 인덱스. 출력: 조합 결과.
    always @(*) begin
        conv_result = {{(ACC_WIDTH-DATA_WIDTH){bias[och][DATA_WIDTH-1]}}, bias[och]}; // Initialize with bias (sign extended)

        for (ich = 0; ich < IN_CHANNELS; ich = ich + 1) begin
            for (ky = 0; ky < KERNEL_SIZE; ky = ky + 1) begin
                for (kx = 0; kx < KERNEL_SIZE; kx = kx + 1) begin
                    in_val = in_feature_map[ich][oy * STRIDE + ky][ox * STRIDE + kx];
                    weight_val = weights[och][ich][ky][kx];
                    product = in_val * weight_val;
                    conv_result = conv_result + (product >>> 4); // Q3.4 × Q3.4 → Q3.4
                end
            end
        end

        // Saturation Logic (clamping to 8-bit signed range)
        if (conv_result > SAT_MAX)
            conv_output = SAT_MAX[DATA_WIDTH-1:0];
        else if (conv_result < SAT_MIN)
            conv_output = SAT_MIN[DATA_WIDTH-1:0];
        else
            conv_output = conv_result[DATA_WIDTH-1:0];

    end

    //========================================================================
    // State Machine and Registers
    //========================================================================
    // 기능: 리셋·상태 전이·출력 저장. 입력: 클록과 제어. 출력: 등록 데이터와 완료 상태.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            och <= 0;
            oy <= 0;
            ox <= 0;
            done <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    done <= 1'b0;
                    och <= 0;
                    oy <= 0;
                    ox <= 0;
                    if (start)
                        state <= COMPUTE;
                end

                COMPUTE: begin
                    // Store computed result
                    out_feature_map[och][oy][ox] <= conv_output;
                    // Update counters
                    if (ox == OUT_WIDTH - 1) begin
                        ox <= 0;
                        if (oy == OUT_HEIGHT - 1) begin
                            oy <= 0;
                            if (och == OUT_CHANNELS - 1) begin
                                state <= FINISH;
                            end else begin
                                och <= och + 1;
                            end
                        end else begin
                            oy <= oy + 1;
                        end
                    end else begin
                        ox <= ox + 1;
                    end
                end

                FINISH: begin
                    done <= 1'b1;
                    state <= IDLE;
                end

                default: begin
                    state <= IDLE;
                    done <= 1'b0;
                end
            endcase
        end
    end

endmodule
