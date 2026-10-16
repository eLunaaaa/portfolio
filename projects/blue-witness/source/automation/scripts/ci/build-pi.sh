#!/usr/bin/env bash
set -euo pipefail

component=${1:?component required}
source scripts/setting/setup_sdk.sh
mkdir -p "dist/$component"

case "$component" in
  sensor)
    sources=(sensor/*.c ipc/ipc.c)
    make -C sensor
    env -u CC -u CXX -u PKG_CONFIG_SYSROOT_DIR -u PKG_CONFIG_LIBDIR \
      make -C sensor check CC=gcc
    binaries=(witness_aruco accident_aruco)
    ;;
  filter)
    sources=(filter/*.c ipc/ipc.c)
    make -C filter
    binaries=(witness_filter)
    ;;
  p2p)
    sources=(p2p/*.c p2p/btp/*.c p2p/denm/*.c ipc/ipc.c)
    make -C p2p all
    binaries=(broadcast_sender broadcast_receiver)
    for launcher in p2p_go.sh p2p_client.sh; do
      install -m 0755 "test/$launcher" "dist/$component/$launcher"
    done
    ;;
  ai)
    sources=(ai/*.cpp ipc/ipc.c)
    ort_root=${ONNXRUNTIME_ROOT:-${RUNNER_TEMP:-/tmp}/onnxruntime-linux-aarch64-1.29.0}
    if [[ ! -f "$ort_root/include/onnxruntime_cxx_api.h" || ! -f "$ort_root/lib/libonnxruntime.so" ]]; then
      curl -fL --retry 3 \
        -o "${RUNNER_TEMP:-/tmp}/onnxruntime.tgz" \
        https://github.com/microsoft/onnxruntime/releases/download/v1.29.0/onnxruntime-linux-aarch64-1.29.0.tgz
      tar -xzf "${RUNNER_TEMP:-/tmp}/onnxruntime.tgz" -C "${RUNNER_TEMP:-/tmp}"
    fi
    make -C ai ONNXRUNTIME_ROOT="$ort_root"
    binaries=(inference video_processor)
    ;;
  time_sync)
    sources=(time_sync/src/*.c ipc/ipc.c)
    make -C time_sync
    binaries=(time_sync)
    ;;
  uploader)
    sources=(uploader/src/*.c uploader/src/common/*.c uploader/src/ingest/*.c \
      uploader/src/queue/*.c uploader/src/worker/*.c ipc/ipc.c)
    make -C uploader
    binaries=(uploader)
    ;;
  *) echo "Unknown Pi component: $component" >&2; exit 2 ;;
esac

# 기존 코드의 경고는 기록하되, cppcheck 결과만으로 배포 artifact를 버리지 않는다.
cppcheck --enable=warning,performance,portability --error-exitcode=0 \
  --suppress=missingIncludeSystem --quiet "${sources[@]}"
for binary in "${binaries[@]}"; do
  readelf -h "test/$binary" | grep -q 'Machine:.*AArch64'
  install -m 0755 "test/$binary" "dist/$component/$binary"
done
