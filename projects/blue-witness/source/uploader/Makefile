#==================================================
# 단일 프로세스 영상 업로더
#==================================================

PROJECT_ROOT = ..

# 제품 빌드 결과만 공용 test 디렉터리에 생성함.
OUTPUT = $(PROJECT_ROOT)/test/uploader

INCLUDE = \
	-Iinclude \
	-I$(PROJECT_ROOT)/ipc

SRCS = \
	src/uploader.c \
	src/common/uploader_log.c \
	src/common/uploader_event_id.c \
	src/ingest/uploader_spool.c \
	src/queue/uploader_queue.c \
	src/worker/uploader_config.c \
	src/worker/uploader_sha256.c \
	src/worker/uploader_ack.c \
	src/worker/uploader_metadata.c \
	src/worker/uploader_http.c \
	src/worker/uploader_tls.c \
	$(PROJECT_ROOT)/ipc/ipc.c

HEADERS = \
	$(wildcard include/*.h) \
	$(PROJECT_ROOT)/ipc/ipc.h \
	$(PROJECT_ROOT)/ipc/protocol.h \
	$(PROJECT_ROOT)/ipc/runtime_watchdog.h \
	$(PROJECT_ROOT)/ipc/uploader_ipc_protocol.h

CFLAGS += \
	-std=c11 \
	-O2 \
	-g \
	-Wall \
	-Wextra \
	-Wpedantic \
	-D_POSIX_C_SOURCE=200809L

LDLIBS = -lsqlite3 -lssl -lcrypto -pthread

all: check-sqlite check-openssl $(OUTPUT)

# SQLite 개발 헤더와 링크용 라이브러리가 SDK sysroot에 모두 있는지 확인함.
check-sqlite:
	@CHECK_BIN="$${TMPDIR:-/tmp}/uploader-sqlite-check.$$$$"; \
	trap 'rm -f "$$CHECK_BIN"' EXIT HUP INT TERM; \
	printf '%s\n' \
		'#include <sqlite3.h>' \
		'int main(void) { return sqlite3_libversion_number() > 0 ? 0 : 1; }' | \
		$(CC) $(CFLAGS) -x c - -lsqlite3 -o "$$CHECK_BIN" || \
		{ echo "ERROR: SDK sysroot cannot compile <sqlite3.h> with -lsqlite3"; exit 1; }

check-openssl:
	@CHECK_BIN="$${TMPDIR:-/tmp}/uploader-openssl-check.$$$$"; \
	trap 'rm -f "$$CHECK_BIN"' EXIT HUP INT TERM; \
	printf '%s\n' \
		'#include <openssl/ssl.h>' \
		'int main(void) { SSL_CTX *ctx = SSL_CTX_new(TLS_client_method()); SSL_CTX_free(ctx); return 0; }' | \
		$(CC) $(CFLAGS) -x c - -lssl -lcrypto -o "$$CHECK_BIN" || \
		{ echo "ERROR: target sysroot needs OpenSSL development headers and -lssl -lcrypto"; exit 1; }

$(OUTPUT): $(SRCS) $(HEADERS)
	mkdir -p $(PROJECT_ROOT)/test
	$(CC) $(CFLAGS) $(INCLUDE) $(SRCS) $(LDLIBS) -o $(OUTPUT)

# 예: make deploy TARGET=eclipse@192.168.50.23
deploy: all
	@test -n "$(TARGET)" || { echo "ERROR: TARGET=user@host가 필요합니다"; exit 2; }
	./deploy/deploy.sh "$(TARGET)" "$(REMOTE_DIR)"

clean:
	rm -f $(OUTPUT)

.PHONY: all clean check-sqlite check-openssl deploy
