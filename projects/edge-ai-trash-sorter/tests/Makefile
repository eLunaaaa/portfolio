CC ?= cc
CFLAGS ?= -std=c11 -Wall -Wextra -Werror -pedantic
.PHONY: test clean
test: test_vision
	./test_vision

test_vision: test_vision.c ../firmware/common/vision_core.c ../firmware/common/vision_core.h
	$(CC) $(CFLAGS) -I../firmware/common test_vision.c ../firmware/common/vision_core.c -lm -o $@
clean:
	rm -f test_vision
