// Micro-benchmark: cost of a thread_local store on Mach-O (TLV descriptor + tlv_get_addr call)
// versus a plain global, as done by Verilated::mtaskId() around every MTask.
//
// Build: clang++ -O2 -std=c++17 tls_cost.cpp -o tls_cost
#include <chrono>
#include <cstdint>
#include <cstdio>

struct ThreadLocal {
    void* contextp = nullptr;
    uint32_t mtaskId = 0;
};
thread_local ThreadLocal t_s;
ThreadLocal g_s;

__attribute__((noinline)) void setTls(uint32_t id) { t_s.mtaskId = id; }
__attribute__((noinline)) void setGlobal(uint32_t id) { g_s.mtaskId = id; }

template <typename F>
static double timeIt(F f) {
    constexpr uint32_t N = 200000000;
    const auto start = std::chrono::steady_clock::now();
    for (uint32_t i = 0; i < N; ++i) f(i);
    return std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start)
               .count()
           / N;
}

int main() {
    std::printf("global store: %.2f ns\n", timeIt(setGlobal));
    std::printf("thread_local store: %.2f ns\n", timeIt(setTls));
    return 0;
}
