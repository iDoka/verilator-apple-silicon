// Micro-benchmark: does a 64-byte stride cause false sharing on Apple Silicon?
// Mimics VlThreadProgress: each thread publishes its own position counter with
// release stores while the others poll theirs. Counters are placed STRIDE bytes apart.
//
// Build: clang++ -O2 -std=c++17 false_sharing.cpp -o false_sharing
// Usage: ./false_sharing <threads> <stride> [qos: ui|default|bg]
#include <pthread.h>
#include <sys/qos.h>

#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <thread>
#include <vector>

static constexpr uint32_t ITERS = 2000000;

static qos_class_t parseQos(const char* s) {
    if (!std::strcmp(s, "ui")) return QOS_CLASS_USER_INTERACTIVE;
    if (!std::strcmp(s, "bg")) return QOS_CLASS_BACKGROUND;
    return QOS_CLASS_DEFAULT;
}

int main(int argc, char** argv) {
    const int nThreads = argc > 1 ? std::atoi(argv[1]) : 4;
    const size_t stride = argc > 2 ? std::atoi(argv[2]) : 64;
    const qos_class_t qos = parseQos(argc > 3 ? argv[3] : "default");
    void* mem = nullptr;
    posix_memalign(&mem, 256, stride * nThreads + 256);
    std::memset(mem, 0, stride * nThreads + 256);
    auto counter = [&](int t) {
        return reinterpret_cast<std::atomic<uint32_t>*>(static_cast<char*>(mem) + t * stride);
    };

    // Ring dependency, like a chain of mtasks on different threads: thread t may publish
    // step i only after thread t-1 published step i (thread 0 waits on the last thread's i-1).
    std::atomic<int> ready{0};
    auto body = [&](int t) {
        pthread_set_qos_class_self_np(qos, 0);
        ready.fetch_add(1);
        while (ready.load() < nThreads) {}
        std::atomic<uint32_t>* const mine = counter(t);
        std::atomic<uint32_t>* const prev = counter((t + nThreads - 1) % nThreads);
        for (uint32_t i = 1; i <= ITERS; ++i) {
            const uint32_t need = t == 0 ? i - 1 : i;
            while (prev->load(std::memory_order_acquire) < need) asm volatile("isb" ::: "memory");
            mine->store(i, std::memory_order_release);
        }
    };
    const auto start = std::chrono::steady_clock::now();
    std::vector<std::thread> threads;
    for (int t = 1; t < nThreads; ++t) threads.emplace_back(body, t);
    body(0);
    for (auto& th : threads) th.join();
    const double ns
        = std::chrono::duration<double, std::nano>(std::chrono::steady_clock::now() - start)
              .count();
    std::printf("threads=%d stride=%zu ns_per_hop=%.1f\n", nThreads, stride,
                ns / (double(ITERS) * nThreads));
    return 0;
}
