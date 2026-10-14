CXX ?= c++
CXXFLAGS ?= -std=c++11 -Wall -Wextra -Werror -pedantic
HEADER := ../firmware/libraries/LoadBalancerCore/src/LoadBalancerCore.h
.PHONY: test clean
test: test_core test_worker
	./test_core
	./test_worker
test_core: test_core.cpp $(HEADER)
	$(CXX) $(CXXFLAGS) -I../firmware/libraries/LoadBalancerCore/src test_core.cpp -o $@
test_worker: test_worker.cpp ../firmware/slave/slave.ino $(HEADER)
	$(CXX) $(CXXFLAGS) -Iavr_stub -I../firmware/libraries/LoadBalancerCore/src test_worker.cpp -o $@
clean:
	rm -f test_core test_worker
