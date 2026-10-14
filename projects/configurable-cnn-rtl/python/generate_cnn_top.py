#!/usr/bin/env python3
# 파일 설명: JSON을 읽어 각 연산 모듈의 포트·데이터 배열·완료 신호를 연결하는 RTL 생성기입니다.
"""JSON 구조를 따라 CNN 연산 계층·배열·start/done을 연결하는 RTL 생성기입니다."""
import argparse
from pathlib import Path
from config_utils import load_config


# 기능: 공간 출력 크기 계산
# 입력: 입력 크기와 계층 목록
# 출력: 최종 공간 크기
# 처리 구조: Conv와 Pool의 커널·stride를 순차 적용
def calculate_feature_map_size(input_size, layers):
    height = width = input_size
    sizes = [(height, width)]
    for layer in layers:
        if layer['type'] == 'conv':
            height = (height - layer['kernel_size']) // layer['stride'] + 1
            width = (width - layer['kernel_size']) // layer['stride'] + 1
        elif layer['type'] == 'pool':
            height = (height - layer['size']) // layer['stride'] + 1
            width = (width - layer['size']) // layer['stride'] + 1
        if height <= 0 or width <= 0:
            raise ValueError('feature map has no elements')
        sizes.append(layer['out_features'] if layer['type'] == 'fc' else (height, width))
    return sizes


# 기능: 설정에 대응하는 top RTL 생성
# 입력: JSON 경로·출력 RTL 경로
# 출력: cnn_top.v 파일
# 처리 구조: 계층별 포트와 연결 생성→채널 우선 flatten→start/done 배선
def generate_cnn_top(config_file, output_file='rtl/cnn_top.v'):
    config, shapes = load_config(config_file)
    ports = [
        'input wire clk', 'input wire rst_n', 'input wire start',
        'input wire [DATA_WIDTH-1:0] input_image [INPUT_SIZE-1:0][INPUT_SIZE-1:0]',
        'output wire [DATA_WIDTH-1:0] class_scores [9:0]', 'output wire done',
    ]
    declarations, instances = [], []
    indices = dict(conv=0, relu=0, pool=0, fc=0)
    previous_data, previous_done = 'input_image', 'start'
    for index, layer in enumerate(config['layers']):
        kind = layer['type']; name = f'{kind}{indices[kind]}'
        indices[kind] += 1
        before, after = shapes[index]['input'], shapes[index]['output']
        if kind in ('conv', 'relu', 'pool'):
            channels, height, width = after
            declarations.append(f'wire [DATA_WIDTH-1:0] {name}_out [{channels-1}:0][{height-1}:0][{width-1}:0];')
        else:
            declarations.append(f'wire [DATA_WIDTH-1:0] {name}_out [{after-1}:0];')
        if kind != 'relu':
            declarations.append(f'wire {name}_done;')
        if kind == 'conv':
            in_channels, in_height, in_width = before
            channels, _, _ = after; kernel = layer['kernel_size']
            ports.extend([
                f'input wire [DATA_WIDTH-1:0] {name}_weights [{channels-1}:0][{in_channels-1}:0][{kernel-1}:0][{kernel-1}:0]',
                f'input wire [DATA_WIDTH-1:0] {name}_bias [{channels-1}:0]',
            ])
            # 첫 단일 채널 영상에 channel 차원을 명시적으로 추가한다.
            if previous_data == 'input_image':
                declarations.extend([
                    'wire [DATA_WIDTH-1:0] input_channels [0:0][INPUT_SIZE-1:0][INPUT_SIZE-1:0];',
                    'genvar image_y, image_x;',
                    'generate for (image_y=0; image_y<INPUT_SIZE; image_y=image_y+1) begin: input_rows',
                    '  for (image_x=0; image_x<INPUT_SIZE; image_x=image_x+1) begin: input_cols',
                    '    assign input_channels[0][image_y][image_x] = input_image[image_y][image_x];',
                    '  end end endgenerate',
                ])
                previous_data = 'input_channels'
            parameters = dict(KERNEL_SIZE=kernel, STRIDE=layer['stride'], IN_CHANNELS=in_channels,
                              OUT_CHANNELS=channels, IN_WIDTH=in_width, IN_HEIGHT=in_height, DATA_WIDTH='DATA_WIDTH')
            connections = dict(clk='clk', rst_n='rst_n', start=previous_done,
                               in_feature_map=previous_data, weights=f'{name}_weights', bias=f'{name}_bias',
                               out_feature_map=f'{name}_out', done=f'{name}_done')
        elif kind == 'relu':
            channels, height, width = before
            parameters = dict(CHANNELS=channels, HEIGHT=height, WIDTH=width, DATA_WIDTH='DATA_WIDTH')
            connections = dict(clk='clk', rst_n='rst_n', in_data=previous_data, out_data=f'{name}_out')
        elif kind == 'pool':
            channels, height, width = before
            parameters = dict(CHANNELS=channels, IN_HEIGHT=height, IN_WIDTH=width,
                              POOL_SIZE=layer['size'], STRIDE=layer['stride'], DATA_WIDTH='DATA_WIDTH')
            connections = dict(clk='clk', rst_n='rst_n', start=previous_done, in_data=previous_data,
                               out_data=f'{name}_out', done=f'{name}_done')
        else:
            features = before if isinstance(before, int) else before[0] * before[1] * before[2]
            if not isinstance(before, int):
                channels, height, width = before
                flattened = f'{name}_input'
                declarations.extend([
                    f'wire [DATA_WIDTH-1:0] {flattened} [{features-1}:0];',
                    f'genvar {name}_ch, {name}_y, {name}_x;',
                    f'generate for ({name}_ch=0; {name}_ch<{channels}; {name}_ch={name}_ch+1) begin: {name}_channels',
                    f'  for ({name}_y=0; {name}_y<{height}; {name}_y={name}_y+1) begin: rows',
                    f'    for ({name}_x=0; {name}_x<{width}; {name}_x={name}_x+1) begin: cols',
                    f'      assign {flattened}[({name}_ch*{height}+{name}_y)*{width}+{name}_x] = {previous_data}[{name}_ch][{name}_y][{name}_x];',
                    '    end end end endgenerate',
                ])
                previous_data = flattened
            ports.extend([
                f'input wire [DATA_WIDTH-1:0] {name}_weights [{after-1}:0][{features-1}:0]',
                f'input wire [DATA_WIDTH-1:0] {name}_bias [{after-1}:0]',
            ])
            parameters = dict(INPUT_SIZE=features, OUTPUT_SIZE=after, DATA_WIDTH='DATA_WIDTH')
            connections = dict(clk='clk', rst_n='rst_n', start=previous_done, in_data=previous_data,
                               weights=f'{name}_weights', bias=f'{name}_bias', out_data=f'{name}_out', done=f'{name}_done')
        parameter_text = ',\n'.join(f'    .{key}({value})' for key,value in parameters.items())
        connection_text = ',\n'.join(f'    .{key}({value})' for key,value in connections.items())
        instances.append(f'{kind}_layer #(\n{parameter_text}\n) {name}_inst (\n{connection_text}\n);')
        previous_data = f'{name}_out'
        if kind != 'relu': previous_done = f'{name}_done'
    code = ('// JSON CNN 구조의 top-level 연결. weights/bias는 입력 port로 제공한다.\n'
            '// 입력: clk/rst_n/start, image, 각 계층 계수. 출력: 10개 점수와 done.\n'
            f'module cnn_top #(parameter DATA_WIDTH=8, parameter INPUT_SIZE={config["input_size"]})(\n'
            + ',\n'.join('    '+port for port in ports) + '\n);\n\n'
            + '\n'.join(declarations) + '\n\n' + '\n\n'.join(instances)
            + f'\n\nassign class_scores = {previous_data};\nassign done = {previous_done};\nendmodule\n')
    output = Path(output_file); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(code, encoding='utf-8')
    print(f'Generated {output} from {config_file}')
    return True


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('config');parser.add_argument('--output',default='rtl/cnn_top.v')
    args=parser.parse_args();generate_cnn_top(args.config,args.output)
