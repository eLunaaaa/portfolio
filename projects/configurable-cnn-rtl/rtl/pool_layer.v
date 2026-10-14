// 파일 설명: 각 공간 창에서 signed 최댓값을 선택하여 특징 맵을 축소합니다.
// 입력: clk, rst_n(0에서 리셋), 데이터 배열과 start.
// 출력: 변환된 데이터 배열과 done(완료 펄스).
// 구조: 파라미터로 배열 크기를 결정하고 조합 연산과 순차 저장을 분리합니다.
//============================================================================
// Module: pool_layer
// Description: Max Pooling Layer
//============================================================================

module pool_layer #(
    parameter CHANNELS = 8,
    parameter IN_HEIGHT = 24,
    parameter IN_WIDTH = 24,
    parameter POOL_SIZE = 2,
    parameter STRIDE = 2,
    parameter DATA_WIDTH = 8
)(
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [DATA_WIDTH-1:0] in_data [CHANNELS-1:0][IN_HEIGHT-1:0][IN_WIDTH-1:0],
    output reg [DATA_WIDTH-1:0] out_data [CHANNELS-1:0][OUT_HEIGHT-1:0][OUT_WIDTH-1:0],
    output reg done
);
    localparam OUT_HEIGHT = (IN_HEIGHT - POOL_SIZE) / STRIDE + 1;
    localparam OUT_WIDTH = (IN_WIDTH - POOL_SIZE) / STRIDE + 1;
    localparam IDLE = 1'b0;
    localparam COMPUTE = 1'b1;

    // Sequential state
    reg state;
    // Combinational signals
    reg signed [DATA_WIDTH-1:0] pool_result [CHANNELS-1:0][OUT_HEIGHT-1:0][OUT_WIDTH-1:0];
    // Loop variables
    integer ch, oy, ox, py, px;
    integer seq_ch, seq_oy, seq_ox;
    reg signed [DATA_WIDTH-1:0] max_val, current_val;
    //========================================================================
    // Max Pooling
    //========================================================================
    // 기능: 현재 위치의 결과 계산. 입력: 데이터·현재 인덱스. 출력: 조합 결과.
    always @(*) begin
        for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
            for (oy = 0; oy < OUT_HEIGHT; oy = oy + 1) begin
                for (ox = 0; ox < OUT_WIDTH; ox = ox + 1) begin
                    // Initialize max_val with the first element of the pooling window
                    max_val = in_data[ch][oy * STRIDE][ox * STRIDE];

                    for (py = 0; py < POOL_SIZE; py = py + 1) begin
                        for (px = 0; px < POOL_SIZE; px = px + 1) begin
                            current_val = in_data[ch][oy * STRIDE + py][ox * STRIDE + px];
                            if (current_val > max_val) begin
                                max_val = current_val;
                            end
                        end
                    end
                    pool_result[ch][oy][ox] = max_val;
                end
            end
        end

    end

    //========================================================================
    // Sequential Logic - State Machine
    //========================================================================
    // 기능: 리셋·상태 전이·출력 저장. 입력: 클록과 제어. 출력: 등록 데이터와 완료 상태.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            done <= 1'b0;
        end else begin
            case (state)
                IDLE: begin
                    done <= 1'b0;
                    if (start)
                        state <= COMPUTE;
                end

                COMPUTE: begin
                    // Store all results
                    for (seq_ch = 0; seq_ch < CHANNELS; seq_ch = seq_ch + 1) begin

                         for (seq_oy = 0; seq_oy < OUT_HEIGHT; seq_oy = seq_oy + 1) begin
                            for (seq_ox = 0; seq_ox < OUT_WIDTH; seq_ox = seq_ox + 1) begin
                                out_data[seq_ch][seq_oy][seq_ox] <= pool_result[seq_ch][seq_oy][seq_ox];
                            end
                        end
                    end

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
