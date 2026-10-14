// 파일 설명: flatten된 특징 벡터를 가중치·bias와 결합하여 분류 점수를 계산합니다.
// 입력: clk, rst_n(0에서 리셋), start, 데이터 배열, 계수와 bias.
// 출력: 변환된 데이터 배열과 done(완료 펄스).
// 구조: 파라미터로 배열 크기를 결정하고 조합 연산과 순차 저장을 분리합니다.
// 수치 형식: signed Q3.4. 곱마다 산술 우측 4비트 이동 후 누적, 최종 signed 범위 포화.
//============================================================================
// Module: fc_layer
// Description: Fully Connected Layer
//============================================================================

module fc_layer #(
    parameter INPUT_SIZE = 1152,
    parameter OUTPUT_SIZE = 10,
    parameter DATA_WIDTH = 8
)(
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [DATA_WIDTH-1:0] in_data [INPUT_SIZE-1:0],
    input wire [DATA_WIDTH-1:0] weights [OUTPUT_SIZE-1:0][INPUT_SIZE-1:0],
    input wire [DATA_WIDTH-1:0] bias [OUTPUT_SIZE-1:0],
    output reg [DATA_WIDTH-1:0] out_data [OUTPUT_SIZE-1:0],
    output reg done
);
    localparam IDLE = 1'b0;
    localparam COMPUTE = 1'b1;

    // Sequential state
    reg state;
    reg [$clog2(OUTPUT_SIZE):0] out_idx;
    // Combinational signals
    localparam ACC_WIDTH = 2 * DATA_WIDTH + $clog2(INPUT_SIZE) + 1;
    localparam signed [ACC_WIDTH-1:0] SAT_MAX = (1 << (DATA_WIDTH-1)) - 1;
    localparam signed [ACC_WIDTH-1:0] SAT_MIN = -(1 << (DATA_WIDTH-1));
    reg signed [ACC_WIDTH-1:0] fc_result;
    reg [DATA_WIDTH-1:0] fc_output;
    // Loop variables
    integer i;
    reg signed [DATA_WIDTH-1:0] in_val, weight_val;
    reg signed [2*DATA_WIDTH-1:0] product;
    //========================================================================
    // Combinational Logic
    //========================================================================
    // 기능: 현재 위치의 결과 계산. 입력: 데이터·현재 인덱스. 출력: 조합 결과.
    always @(*) begin
        fc_result = {{(ACC_WIDTH-DATA_WIDTH){bias[out_idx][DATA_WIDTH-1]}}, bias[out_idx]}; // Initialize with bias (sign extended)

        for (i = 0; i < INPUT_SIZE; i = i + 1) begin
            in_val = in_data[i];
            weight_val = weights[out_idx][i];
            product = in_val * weight_val;
            fc_result = fc_result + (product >>> 4); // Q3.4 × Q3.4 → Q3.4
        end

        // Saturation Logic
        if (fc_result > SAT_MAX)
            fc_output = SAT_MAX[DATA_WIDTH-1:0];
        else if (fc_result < SAT_MIN)
            fc_output = SAT_MIN[DATA_WIDTH-1:0];
        else
            fc_output = fc_result[DATA_WIDTH-1:0];

    end

    //========================================================================
    // State Machine
    //========================================================================
    // 기능: 리셋·상태 전이·출력 저장. 입력: 클록과 제어. 출력: 등록 데이터와 완료 상태.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            out_idx <= 0;
            done <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    done <= 1'b0;
                    out_idx <= 0;
                    if (start)
                        state <= COMPUTE;
                end

                COMPUTE: begin
                    // Store computed result
                    out_data[out_idx] <= fc_output;
                    // Check if all outputs done
                    if (out_idx == OUTPUT_SIZE - 1) begin
                        done <= 1'b1;
                        state <= IDLE;
                        out_idx <= 0;
                    end else begin
                        out_idx <= out_idx + 1;
                    end
                end

                default: begin
                    state <= IDLE;
                    out_idx <= 0;
                    done <= 1'b0;
                end
            endcase
        end
    end

endmodule
