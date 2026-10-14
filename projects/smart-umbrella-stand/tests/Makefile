CC ?= cc
CFLAGS := -std=c11 -Wall -Wextra -Werror -pedantic \
	-I../firmware/common/include -I../firmware/master/Core/Inc
SOURCES := test_core.c ../firmware/common/src/protocol.c \
	../firmware/common/src/date_utils.c ../firmware/master/Core/Src/control_logic.c \
	../firmware/slave2/Core/Src/slave2_spi.c
CFLAGS += -I../firmware/slave2/Core/Inc

.PHONY: test clean
test: test_core
	./test_core

test_core: $(SOURCES)
	$(CC) $(CFLAGS) $(SOURCES) -o $@

clean:
	$(RM) test_core
