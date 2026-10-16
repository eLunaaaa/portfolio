CC ?= gcc
CXX ?= g++
PKG_CONFIG ?= pkg-config
ONNXRUNTIME_ROOT ?= /opt/onnxruntime
ONNXRUNTIME_RUNPATH ?= /opt/onnxruntime/lib
OUTDIR := ../test
INFERENCE_BIN := $(OUTDIR)/inference
VIDEO_PROCESSOR_BIN := $(OUTDIR)/video_processor
IPC_SRC := ../ipc/ipc.c
IPC_OBJ := ipc.o
COMMON_IPC_HEADERS := ../ipc/ipc.h ../ipc/protocol.h ../ipc/runtime_watchdog.h \
	../ipc/time_sync_protocol.h ../ipc/uploader_ipc_protocol.h
PKGS := libcamera libavcodec libavformat libavutil
GPIO_PKG := libgpiod
COMMON_CPPFLAGS := $(shell $(PKG_CONFIG) --cflags $(PKGS)) -I../ipc
INFERENCE_CPPFLAGS = $(COMMON_CPPFLAGS) \
	$(shell $(PKG_CONFIG) --cflags $(GPIO_PKG)) -I$(ONNXRUNTIME_ROOT)/include
CXXFLAGS += -std=c++17 -O2 -Wall -Wextra -Wpedantic -pthread
COMMON_LDLIBS := $(shell $(PKG_CONFIG) --libs $(PKGS)) -pthread $(LDLIBS)
INFERENCE_LDFLAGS := $(LDFLAGS) -L$(ONNXRUNTIME_ROOT)/lib \
	-Wl,-rpath,$(ONNXRUNTIME_RUNPATH)
INFERENCE_LDLIBS = $(COMMON_LDLIBS) \
	$(shell $(PKG_CONFIG) --libs $(GPIO_PKG)) -lonnxruntime

.PHONY: all clean inference video_processor
all: $(INFERENCE_BIN) $(VIDEO_PROCESSOR_BIN)
inference: $(INFERENCE_BIN)
video_processor: $(VIDEO_PROCESSOR_BIN)

$(OUTDIR):
	mkdir -p $@

$(INFERENCE_BIN): inference.o $(IPC_OBJ) | $(OUTDIR)
	$(CXX) $(CXXFLAGS) $(INFERENCE_LDFLAGS) $^ $(INFERENCE_LDLIBS) -o $@

$(VIDEO_PROCESSOR_BIN): video_processor.o $(IPC_OBJ) | $(OUTDIR)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) $^ $(COMMON_LDLIBS) -o $@

inference.o: inference.cpp inference_config.h evidence_handoff.h runtime_support.h $(COMMON_IPC_HEADERS) ../ipc/inference_protocol.h
	$(CXX) $(CPPFLAGS) $(INFERENCE_CPPFLAGS) $(CXXFLAGS) -c $< -o $@

video_processor.o: video_processor.cpp evidence_handoff.h runtime_support.h $(COMMON_IPC_HEADERS) \
	../ipc/video_processor_protocol.h
	$(CXX) $(CPPFLAGS) $(COMMON_CPPFLAGS) $(CXXFLAGS) -c $< -o $@

$(IPC_OBJ): $(IPC_SRC) ../ipc/ipc.h ../ipc/protocol.h
	$(CC) $(CPPFLAGS) -I../ipc -std=c11 -O2 -Wall -Wextra -Wpedantic \
		-c $(IPC_SRC) -o $@

clean:
	rm -f inference.o video_processor.o $(IPC_OBJ) \
		$(INFERENCE_BIN) $(VIDEO_PROCESSOR_BIN) inference video_processor \
		accident_ai main.o camera_capture.o ring_buffer.o event_input.o video_writer.o
