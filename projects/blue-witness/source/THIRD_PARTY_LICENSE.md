# Third-Party Licenses

본 프로젝트는 아래의 오픈소스 소프트웨어 및 표준을 사용합니다.

각 서드파티 구성요소에는 해당 구성요소의 원래 라이선스 및 저작권 조건이 적용됩니다. 본 문서는 프로젝트 자체 소스 코드의 라이선스를 변경하지 않습니다.


## 1. OpenCV

- License: Apache License 2.0
- Usage: AI 파이프라인의 이미지 및 영상 처리
- Version: 프로젝트의 빌드/의존성 설정에 따름
- Source: https://opencv.org/

OpenCV 4.5.0 이상 버전은 Apache License 2.0으로 배포됩니다.


## 2. YOLOX

- License: Apache License 2.0
- Usage: 경량 객체 검출
- Source: https://github.com/Megvii-BaseDetection/YOLOX


## 3. asn1c

- License: BSD-2-Clause
- Usage: ASN.1 정의로부터 C 소스 및 헤더 파일을 생성하기 위한 컴파일러
- Source: https://github.com/vlm/asn1c

asn1c는 빌드 및 코드 생성 도구로 사용됩니다.

본 프로젝트에 포함된 생성된 C/H 파일은 수정된 ASN.1 정의를 기반으로 생성되었습니다.


## 4. ETSI DENM ASN.1

- Standard: ETSI DENM TS 103 831
- Version: v2.1.1
- License: BSD-3-Clause
- Usage: DENM 메시지 정의 및 C/H 코드 생성
- Source: https://forge.etsi.org/rep/ITS/asn1/denm_ts103831

원본 ASN.1 정의를 코드 생성 전에 프로젝트 요구사항에 맞게 일부 수정하였습니다.

원본 ETSI ASN.1 자료에 적용되는 저작권 및 라이선스 고지는 유지됩니다.


## 5. ETSI ITS Common Data Dictionary (CDD)

- Standard: ETSI TS 102 894-2
- Version: v2.1.1
- License: BSD-3-Clause
- Usage: DENM ASN.1 정의에서 사용하는 공통 ITS 데이터 정의
- Source: https://forge.etsi.org/rep/ITS/asn1/cdd_ts102894_2

CDD ASN.1 정의는 DENM ASN.1 정의의 입력 의존성으로 사용되었으며, 이를 기반으로 프로젝트의 C/H 소스 파일을 생성하였습니다.


## 6. FastAPI

- License: MIT License
- Usage: Evidence Server의 REST API 및 HTTP 요청/응답 처리
- Source: https://github.com/fastapi/fastapi


## 7. Uvicorn

- License: BSD-3-Clause
- Usage: Evidence Server의 ASGI 애플리케이션 실행
- Source: https://github.com/Kludex/uvicorn


## 8. python-multipart

- License: Apache License 2.0
- Usage: MP4 영상 및 메타데이터 업로드를 위한 multipart/form-data 처리
- Source: https://github.com/Kludex/python-multipart


## 9. Python

- License: Python Software Foundation License
- Usage: Evidence Server 실행 환경
- Source: https://www.python.org/


## 10. SQLite

- License: Public Domain
- Usage: 사고 기록, 증거 영상 메타데이터, 사용자, 차량 및 인증 정보 저장
- Source: https://sqlite.org/


## 11. FFmpeg

- License: LGPL v2.1
- Usage:
  - 다중 시점 영상 Timestamp 정렬
  - 영상 자르기
  - 영상 배치
  - MP4 병합
- Distribution: 동적으로 링크하여 사용합니다.


## 12. chrony

- License: GNU General Public License v2
- Usage:
  - Raspberry Pi 시스템 시각 동기화
  - 시간 동기화 오차 측정
- Distribution: Raspberry Pi 타깃 환경에 apt install로 설치하여 시간 동기화에만 사용합니다.


## 13. cloudflared

- License: Apache License 2.0
- Usage: Cloudflare Quick Tunnel을 이용한 HTTPS 외부 시연 접속
- Distribution: 서버/타깃 환경에 별도로 설치합니다.
- Source: https://github.com/cloudflare/cloudflared


## 14. BlueZ

- License: GNU General Public License v2 or later (GPL-2.0-or-later)
- Usage:
  - Raspberry Pi의 BLE GATT 서비스 등록 및 광고
  - Windows 송신 PC와 최초 페어링 및 trust 설정
- Distribution: Raspberry Pi에 apt로 설치하는 Bluetooth 서비스 및 도구입니다. 센서 프로세스는 별도로 실행되는 bluetoothd와 공개 D-Bus API로 통신합니다.
- Source: https://github.com/bluez/bluez


## 15. GLib / GIO / GObject

- License: GNU Lesser General Public License v2.1 or later (LGPL-2.1-or-later)
- Usage: 센서 프로세스의 GDBus 통신, GATT 인터페이스 등록 및 비동기 이벤트 처리
- Distribution: 센서 실행 파일에서 gio-2.0을 통해 동적으로 링크하여 사용합니다.
- Source: https://gitlab.gnome.org/GNOME/glib


## 16. OpenSSL

- License: Apache License 2.0 (OpenSSL 3.0 이상)
- Usage:
  - Uploader의 HTTPS/TLS 클라이언트 전송
  - 공개 CA를 이용한 서버 인증서 및 IP/DNS 이름 검증
- Distribution: Raspberry Pi 타깃 환경에 `openssl`과 `libssl-dev`를 apt로 설치합니다. Uploader 실행 파일은 `libssl.so.3` 및 `libcrypto.so.3`에 동적으로 링크하며, OpenSSL 소스나 private key를 저장소에 포함하지 않습니다.
- Source: [https://openssl-library.org/](https://openssl-library.org/)
- License text: [https://openssl-library.org/source/license/](https://openssl-library.org/source/license/)

OpenSSL 패키지 또는 이를 포함한 시스템 이미지를 배포할 때는 해당 패키지에 포함된 저작권·라이선스·NOTICE 고지를 함께 유지합니다.

# Development and Build Tools

다음 도구는 프로젝트 런타임 의존성이 아니라 개발 및 빌드 과정에서 사용하는 도구이므로 본 프로젝트의 런타임 서드파티 구성요소 목록에서는 별도로 관리하지 않습니다.

- GCC
- GNU Make
- Git
- Ubuntu 개발 패키지


# Notes

본 문서는 프로젝트에서 직접 사용하는 주요 서드파티 소프트웨어 및 표준을 정리한 것입니다.

각 서드파티 구성요소의 전이 의존성(transitive dependencies)은 모두 나열하지 않으며, 각각 고유한 라이선스 및 조건을 가질 수 있습니다.

서드파티 구성요소를 수정하거나 배포하는 경우 해당 구성요소의 원래 라이선스와 저작권 고지 및 기타 요구사항을 준수합니다.
