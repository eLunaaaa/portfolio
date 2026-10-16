#==================================================
# Chrony 상태 제공 프로세스
#==================================================

PROJECT_ROOT = ..
OUTPUT = $(PROJECT_ROOT)/test/time_sync

INCLUDE = -I$(PROJECT_ROOT)/ipc

SRCS = \
	src/time_sync.c \
	$(PROJECT_ROOT)/ipc/ipc.c

CFLAGS += \
	-std=c11 \
	-O2 \
	-g \
	-Wall \
	-Wextra \
	-Wpedantic \
	-D_POSIX_C_SOURCE=200809L

LDLIBS = -pthread -lm

# 기본 빌드는 제품 실행 파일 하나만 생성함.
all: $(OUTPUT)

$(OUTPUT): $(SRCS) \
		$(PROJECT_ROOT)/ipc/ipc.h \
		$(PROJECT_ROOT)/ipc/protocol.h \
		$(PROJECT_ROOT)/ipc/runtime_watchdog.h \
		$(PROJECT_ROOT)/ipc/time_sync_protocol.h
	mkdir -p $(PROJECT_ROOT)/test
	$(CC) $(CFLAGS) $(INCLUDE) $(SRCS) $(LDLIBS) -o $(OUTPUT)

clean:
	rm -f $(OUTPUT) $(PROJECT_ROOT)/test/time-sync

# 예: make deploy TARGET=eclipse@192.168.50.23
deploy: all
	@test -n "$(TARGET)" || { echo "ERROR: TARGET=user@host가 필요합니다"; exit 2; }
	./deploy/deploy.sh "$(TARGET)" "$(REMOTE_DIR)"

.PHONY: all clean deploy
