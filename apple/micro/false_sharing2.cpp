// Micro-benchmark: independent per-thread counters STRIDE bytes apart; no true sharing at all.
// Any slowdown at a given stride means the counters share a coherence granule.
// Build: clang++ -O2 -std=c++17 false_sharing2.cpp -o false_sharing2
// Usage: ./false_sharing2 <threads> <stride>
#include <pthread.h>
#include <sys/qos.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <vector>

int main(int argc, char** argv) {
    const int nThreads = std::atoi(argv[1]);
    const size_t stride = std::atoi(argv[2]);
    constexpr uint32_t ITERS = 50000000;
    alignas(256) static char mem[4096];
    std::atomic<int> ready{0};
    auto body = [&](int t) {
        pthread_set_qos_class_self_np(std::getenv("BG") ? QOS_CLASS_BACKGROUND : QOS_CLASS_USER_INTERACTIVE, 0);
        auto* const c = reinterpret_cast<std::atomic<uint32_t>*>(mem + t * stride);
        ready.fetch_add(1);
        while (ready.load() < nThreads) {}
        if (std::getenv("RMW")) {
            for (uint32_t i = 0; i < ITERS; ++i) c->fetch_add(1, std::memory_order_relaxed);
        } else {
            for (uint32_t i = 0; i < ITERS; ++i) {
                c->store(i, std::memory_order_release);
                asm volatile("" ::: "memory");  // Keep every store
            }
        }
    };
    const auto start = std::chrono::steady_clock::now();
    std::vector<std::thread> threads;
    for (int t = 0; t < nThreads; ++t) threads.emplace_back(body, t);
    for (auto& th : threads) th.join();
    const double ns
        = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start)
              .count();
    std::printf("threads=%d stride=%zu ns_per_store=%.2f\n", nThreads, stride, ns / ITERS);
}
