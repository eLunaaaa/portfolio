// 파일 역할: 학습한 baseline 가중치·MNIST 입력의 정수 출력 100개를 RTL과 비교합니다.
// 입력: tests/vectors/baseline.mem. 출력: bit-exact PASS 또는 $fatal.
// 구조·이유: 파라미터 변환→CHW 입력→완료 대기→10개 클래스 점수 비교를 10회 수행합니다.
module tb_fixed_point;
reg clk=0;always #5 clk=~clk;
reg rst_n=0,start=0;
reg [7:0] image[14:0][14:0];
reg [7:0] cw[1:0][0:0][2:0][2:0],cb[1:0];
reg [7:0] fw[9:0][71:0],fb[9:0];
reg [7:0] data[0:3099];
wire [7:0] scores[9:0];wire done;
cnn_top dut(.clk(clk),.rst_n(rst_n),.start(start),.input_image(image),.class_scores(scores),.done(done),.conv0_weights(cw),.conv0_bias(cb),.fc0_weights(fw),.fc0_bias(fb));
integer offset=0,c,y,x,i,j,n;
initial begin
$readmemh("tests/vectors/baseline.mem",data);
for(c=0;c<2;c=c+1)for(y=0;y<3;y=y+1)for(x=0;x<3;x=x+1)begin cw[c][0][y][x]=data[offset];offset=offset+1;end
for(c=0;c<2;c=c+1)begin cb[c]=data[offset];offset=offset+1;end
for(i=0;i<10;i=i+1)for(j=0;j<72;j=j+1)begin fw[i][j]=data[offset];offset=offset+1;end
for(i=0;i<10;i=i+1)begin fb[i]=data[offset];offset=offset+1;end
#12;rst_n=1;
for(n=0;n<10;n=n+1)begin
for(y=0;y<15;y=y+1)for(x=0;x<15;x=x+1)begin image[y][x]=data[offset];offset=offset+1;end
@(negedge clk);start=1;@(negedge clk);start=0;
wait(done===1);#1;
for(i=0;i<10;i=i+1)begin
if(scores[i]!==data[offset])$fatal(1,"sample %0d class %0d: RTL %h reference %h",n,i,scores[i],data[offset]);
offset=offset+1;
end
@(posedge clk);#1;
end
$display("MNIST baseline: 100/100 class scores bit-exact PASS");$finish;
end
initial begin #1000000;$fatal(1,"완료 시간 초과");end
endmodule
