"""파일 역할: RTL과 같은 signed Q3.4 정수 연산을 Python에서 계산합니다.
입력: 정수 이미지/계수/계층 설정. 출력: 포화된 정수 점수.
이유: 실수 모델의 출력 반올림 평가와 RTL의 계수 양자화·항별 shift를 구분해 비트 단위 비교합니다.
"""
import numpy as np


def quantize(values):
    """기능: 실수를 Q3.4 정수로 변환. 입력: 배열. 출력: int64 배열.
    구조·이유: 1/16 단위 반올림 후 −128~127로 포화해 RTL의 8비트 계수를 만듭니다.
    """
    return np.clip(np.rint(np.asarray(values)*16),-128,127).astype(np.int64)


def saturate(values):
    """기능: signed 8비트 포화. 입력: 누산값. 출력: int64 배열.
    구조·이유: 래핑 대신 RTL의 SAT_MIN/SAT_MAX와 같은 범위로 제한합니다.
    """
    return np.clip(values,-128,127).astype(np.int64)


def forward(image,layers,parameters):
    """기능: 정수 CNN 기준 계산. 입력: CHW 이미지·계층 목록·가중치/bias 순서 목록.
    출력: 최종 정수 벡터. 구조·이유: 곱마다 산술 우측 4비트 이동 후 합산해 RTL과 일치시킵니다.
    """
    output=np.asarray(image,dtype=np.int64);index=0
    for layer in layers:
        kind=layer['type']
        if kind=='conv':
            weights,bias=parameters[index];index+=1
            kernel,stride=layer['kernel_size'],layer['stride']
            height=(output.shape[1]-kernel)//stride+1;width=(output.shape[2]-kernel)//stride+1
            result=np.zeros((weights.shape[0],height,width),dtype=np.int64)
            for channel in range(weights.shape[0]):
                for y in range(height):
                    for x in range(width):
                        window=output[:,y*stride:y*stride+kernel,x*stride:x*stride+kernel]
                        result[channel,y,x]=bias[channel]+np.sum((window*weights[channel])>>4)
            output=saturate(result)
        elif kind=='relu':output=np.maximum(output,0)
        elif kind=='pool':
            kernel,stride=layer['kernel_size'] if 'kernel_size' in layer else layer['size'],layer['stride']
            height=(output.shape[1]-kernel)//stride+1;width=(output.shape[2]-kernel)//stride+1
            result=np.zeros((output.shape[0],height,width),dtype=np.int64)
            for y in range(height):
                for x in range(width):result[:,y,x]=output[:,y*stride:y*stride+kernel,x*stride:x*stride+kernel].max(axis=(1,2))
            output=result
        elif kind=='fc':
            weights,bias=parameters[index];index+=1
            output=saturate(((weights*output.reshape(1,-1))>>4).sum(axis=1)+bias)
        else:raise ValueError('지원하지 않는 계층: '+kind)
    return output
