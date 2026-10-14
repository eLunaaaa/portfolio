// 파일 설명: baseline 전체 계층의 채널 flatten과 start/done 전달을 검사합니다.
// 입력: 상수 bias로 만든 2개 채널과 채널별 특징을 선택하는 FC 계수.
// 출력: 10개 점수 기대값 검사. 구조: 초기화→실행→완료→출력 비교.
module tb_cnn_top;
reg clk=0; always #5 clk=~clk;
reg rst_n=0, start=0;
reg [7:0] image [14:0][14:0];
reg [7:0] cw [1:0][0:0][2:0][2:0];
reg [7:0] cb [1:0];
reg [7:0] fw [9:0][71:0];
reg [7:0] fb [9:0];
wire [7:0] scores [9:0]; wire done;
cnn_top dut(.clk(clk),.rst_n(rst_n),.start(start),.input_image(image),
.class_scores(scores),.done(done),.conv0_weights(cw),.conv0_bias(cb),.fc0_weights(fw),.fc0_bias(fb));
integer i,j,k;
initial begin
for(i=0;i<15;i=i+1)for(j=0;j<15;j=j+1)image[i][j]=0;
for(i=0;i<2;i=i+1)for(j=0;j<3;j=j+1)for(k=0;k<3;k=k+1)cw[i][0][j][k]=0;
cb[0]=16; cb[1]=32;
for(i=0;i<10;i=i+1)begin
fb[i]=i;
for(j=0;j<72;j=j+1)fw[i][j]=0;
if(i%2==0)fw[i][0]=16;else fw[i][36]=16;
end
#12; rst_n=1;
@(negedge clk); start=1;
@(negedge clk); start=0;
wait(done); #1;
for(i=0;i<10;i=i+1)begin
if(scores[i] !== (i+(i%2==0?16:32)))$fatal(1,"top 점수 불일치 %0d: %0d",i,scores[i]);
end
$display("baseline top 연결 PASS");$finish;
end
initial begin #10000;$fatal(1,"top 완료 시간 초과");end
endmodule
