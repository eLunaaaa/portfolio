// JSON CNN 구조의 top-level 연결. weights/bias는 입력 port로 제공한다.
// 입력: clk/rst_n/start, image, 각 계층 계수. 출력: 10개 점수와 done.
module cnn_top #(parameter DATA_WIDTH=8, parameter INPUT_SIZE=15)(
    input wire clk,
    input wire rst_n,
    input wire start,
    input wire [DATA_WIDTH-1:0] input_image [INPUT_SIZE-1:0][INPUT_SIZE-1:0],
    output wire [DATA_WIDTH-1:0] class_scores [9:0],
    output wire done,
    input wire [DATA_WIDTH-1:0] conv0_weights [1:0][0:0][2:0][2:0],
    input wire [DATA_WIDTH-1:0] conv0_bias [1:0],
    input wire [DATA_WIDTH-1:0] fc0_weights [9:0][71:0],
    input wire [DATA_WIDTH-1:0] fc0_bias [9:0]
);

wire [DATA_WIDTH-1:0] conv0_out [1:0][12:0][12:0];
wire conv0_done;
wire [DATA_WIDTH-1:0] input_channels [0:0][INPUT_SIZE-1:0][INPUT_SIZE-1:0];
genvar image_y, image_x;
generate for (image_y=0; image_y<INPUT_SIZE; image_y=image_y+1) begin: input_rows
  for (image_x=0; image_x<INPUT_SIZE; image_x=image_x+1) begin: input_cols
    assign input_channels[0][image_y][image_x] = input_image[image_y][image_x];
  end end endgenerate
wire [DATA_WIDTH-1:0] relu0_out [1:0][12:0][12:0];
wire [DATA_WIDTH-1:0] pool0_out [1:0][5:0][5:0];
wire pool0_done;
wire [DATA_WIDTH-1:0] fc0_out [9:0];
wire fc0_done;
wire [DATA_WIDTH-1:0] fc0_input [71:0];
genvar fc0_ch, fc0_y, fc0_x;
generate for (fc0_ch=0; fc0_ch<2; fc0_ch=fc0_ch+1) begin: fc0_channels
  for (fc0_y=0; fc0_y<6; fc0_y=fc0_y+1) begin: rows
    for (fc0_x=0; fc0_x<6; fc0_x=fc0_x+1) begin: cols
      assign fc0_input[(fc0_ch*6+fc0_y)*6+fc0_x] = pool0_out[fc0_ch][fc0_y][fc0_x];
    end end end endgenerate

conv_layer #(
    .KERNEL_SIZE(3),
    .STRIDE(1),
    .IN_CHANNELS(1),
    .OUT_CHANNELS(2),
    .IN_WIDTH(15),
    .IN_HEIGHT(15),
    .DATA_WIDTH(DATA_WIDTH)
) conv0_inst (
    .clk(clk),
    .rst_n(rst_n),
    .start(start),
    .in_feature_map(input_channels),
    .weights(conv0_weights),
    .bias(conv0_bias),
    .out_feature_map(conv0_out),
    .done(conv0_done)
);

relu_layer #(
    .CHANNELS(2),
    .HEIGHT(13),
    .WIDTH(13),
    .DATA_WIDTH(DATA_WIDTH)
) relu0_inst (
    .clk(clk),
    .rst_n(rst_n),
    .in_data(conv0_out),
    .out_data(relu0_out)
);

pool_layer #(
    .CHANNELS(2),
    .IN_HEIGHT(13),
    .IN_WIDTH(13),
    .POOL_SIZE(2),
    .STRIDE(2),
    .DATA_WIDTH(DATA_WIDTH)
) pool0_inst (
    .clk(clk),
    .rst_n(rst_n),
    .start(conv0_done),
    .in_data(relu0_out),
    .out_data(pool0_out),
    .done(pool0_done)
);

fc_layer #(
    .INPUT_SIZE(72),
    .OUTPUT_SIZE(10),
    .DATA_WIDTH(DATA_WIDTH)
) fc0_inst (
    .clk(clk),
    .rst_n(rst_n),
    .start(pool0_done),
    .in_data(fc0_input),
    .weights(fc0_weights),
    .bias(fc0_bias),
    .out_data(fc0_out),
    .done(fc0_done)
);

assign class_scores = fc0_out;
assign done = fc0_done;
endmodule
