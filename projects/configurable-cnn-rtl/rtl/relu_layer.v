// 파일 설명: 음수 값을 0으로 바꾸는 활성화 연산을 한 클록에 등록합니다.
// 입력: clk, rst_n(0에서 리셋), 데이터 배열.
// 출력: 변환된 데이터 배열.
// 구조: 파라미터로 배열 크기를 결정하고 조합 연산과 순차 저장을 분리합니다.
//============================================================================
// Module: relu_layer
// Description: ReLU Activation
//============================================================================

module relu_layer #(
    parameter CHANNELS = 8,
    parameter HEIGHT = 28,
    parameter WIDTH = 28,
    parameter DATA_WIDTH = 8
)(
    input wire clk,
    input wire rst_n,
    input wire [DATA_WIDTH-1:0] in_data [CHANNELS-1:0][HEIGHT-1:0][WIDTH-1:0],
    output reg [DATA_WIDTH-1:0] out_data [CHANNELS-1:0][HEIGHT-1:0][WIDTH-1:0]
);
    integer ch, y, x;

    // 기능: 리셋·상태 전이·출력 저장. 입력: 클록과 제어. 출력: 등록 데이터와 완료 상태.
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (ch = 0; ch < CHANNELS; ch = ch + 1)
                for (y = 0; y < HEIGHT; y = y + 1)
                    for (x = 0; x < WIDTH; x = x + 1)
                        out_data[ch][y][x] <= 8'h00;
        end else begin
            // ReLU: max(0, x)
            for (ch = 0; ch < CHANNELS; ch = ch + 1) begin
                for (y = 0; y < HEIGHT; y = y + 1) begin
                    for (x = 0; x < WIDTH; x = x + 1) begin
                        if ($signed(in_data[ch][y][x]) > 0)
                            out_data[ch][y][x] <= in_data[ch][y][x];
                        else
                            out_data[ch][y][x] <= 8'h00;
                    end
                end
            end

        end
    end

endmodule
