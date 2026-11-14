// Host for a model translated by vl2metal.py: initializes one instance per seed on the CPU
// model, runs them on the GPU, reports results and throughput, and checks GPU state against
// the CPU model.
//
// Build (see build.sh): include the model's obj_dir and the vl2metal output directory, link the
// model's objects.
//
// Seeds: instance i runs as the CPU model would with +verilator+seed+<seed>: its initial
// blocks run on the CPU with that seed, then the GPU continues with the state of the CPU
// model's random generator. If the initial blocks of the first seed draw no random numbers,
// all seeds start from its state and only their generators differ, which saves running the
// initial blocks (e.g. $readmemh) per seed. Plusargs are not passed to the model.
//
// Usage: host <sim.metallib> <instances> [options]
//   --first-seed S  instance i uses seed S+i (default 1)
//   --seeds FILE    seeds, one per line; replaces <instances> and --first-seed
//   --results FILE  write "<seed> <PASS|FAIL|TIMEOUT|ERROR> <cycles>" for each instance
//   --pass N        value of the status member that means the test passed (default 0xff)
//   --half N        evaluations per dispatch (default 2000); the clock toggles before each
//   --per-cb N      dispatches per command buffer (default 8)
//   --max-half N    stop after this many evaluations per instance (default 1e9)
//   --tg N          threads per threadgroup (default 64)
//   --verify N      check this many instances against the CPU model (default 2)
//   --stagger N     benchmark of divergence: all instances run the first seed, instance i
//                   starts N*i evaluations later in the test
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include "verilated.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <memory>
#include <string>
#include <vector>

#include "gpu_root.h"

using Model = Vl2MetalModel;

double sc_time_stamp() { return 0; }

static double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

struct CpuModel {
    std::unique_ptr<VerilatedContext> ctx{new VerilatedContext};
    std::unique_ptr<Model> model;
    uint64_t evals = 0;
    explicit CpuModel(int seed) {
        // The random generator is per thread; seeding bumps its epoch, so it restarts
        Verilated::threadContextp(ctx.get());
        ctx->randSeed(seed);
        model.reset(new Model{ctx.get()});
    }
    bool step() {  // Same as the testbench loop body; false once $finish ran
        if (ctx->gotFinish()) return false;
        model->VL2METAL_CLOCK = !model->VL2METAL_CLOCK;
        model->eval();
        ++evals;
        return true;
    }
};

// State of this thread's random generator, which the CPU model draws from
static void threadRngState(uint64_t (&state)[2]) {
    const std::string s = VlRNG::vl_thread_rng().get_randstate();  // 'a'-based hex of the bytes
    uint8_t bytes[sizeof(state)];
    for (size_t i = 0; i < sizeof(bytes); ++i)
        bytes[i] = ((s[1 + 2 * i] - 'a') << 4) | (s[2 + 2 * i] - 'a');
    std::memcpy(state, bytes, sizeof(bytes));
}

static void setLaneRng(vl2metal::Root* tiles, size_t i) {
    uint64_t state[2];
    threadRngState(state);
    vl2metal::Root& t = tiles[i / vl2metal::kLanes];
    t.__Vgpu_rng0[i % vl2metal::kLanes] = state[0];
    t.__Vgpu_rng1[i % vl2metal::kLanes] = state[1];
}

int main(int argc, char** argv) {
    if (argc < 3) {
        fprintf(stderr, "usage: %s <sim.metallib> <instances> [options]\n", argv[0]);
        return 1;
    }
    const char* libPath = argv[1];
    size_t instances = std::strtoul(argv[2], nullptr, 0);
    uint32_t half = 2000, perCb = 8, tgSize = 64;
    uint64_t maxHalf = 1000000000, stagger = 0, passValue = 0xff;
    size_t verify = 2;
    long firstSeed = 1;
    std::string seedsFile, resultsFile;
    for (int i = 3; i + 1 < argc; i += 2) {
        const std::string opt = argv[i];
        const uint64_t v = std::strtoull(argv[i + 1], nullptr, 0);
        if (opt == "--first-seed") firstSeed = std::strtol(argv[i + 1], nullptr, 0);
        else if (opt == "--seeds") seedsFile = argv[i + 1];
        else if (opt == "--results") resultsFile = argv[i + 1];
        else if (opt == "--pass") passValue = v;
        else if (opt == "--half") half = v;
        else if (opt == "--per-cb") perCb = v;
        else if (opt == "--max-half") maxHalf = v;
        else if (opt == "--stagger") stagger = v;
        else if (opt == "--tg") tgSize = v;
        else if (opt == "--verify") verify = v;
        else {
            fprintf(stderr, "unknown option %s\n", opt.c_str());
            return 1;
        }
    }
    std::vector<int> seeds;
    if (seedsFile.empty()) {
        for (size_t i = 0; i < instances; ++i) seeds.push_back(static_cast<int>(firstSeed + i));
    } else {
        std::ifstream in{seedsFile};
        for (long s; in >> s;) seeds.push_back(static_cast<int>(s));
        instances = seeds.size();
    }
    if (!instances) {
        fprintf(stderr, "no instances\n");
        return 1;
    }
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        NSError* err = nil;
        double t0 = now();
        id<MTLLibrary> lib = [dev newLibraryWithURL:[NSURL fileURLWithPath:@(libPath)] error:&err];
        if (!lib) {
            fprintf(stderr, "library: %s\n", err.localizedDescription.UTF8String);
            return 1;
        }
        id<MTLComputePipelineState> run = [dev
            newComputePipelineStateWithFunction:[lib newFunctionWithName:@"vl2metal_run"]
                                          error:&err];
        if (!run) {
            fprintf(stderr, "pipeline: %s\n", err.localizedDescription.UTF8String);
            return 1;
        }
        printf("pipeline: %.1f s, maxThreadsPerThreadgroup %lu, threadExecutionWidth %lu\n",
               now() - t0, (unsigned long)run.maxTotalThreadsPerThreadgroup,
               (unsigned long)run.threadExecutionWidth);
        id<MTLCommandQueue> queue = [dev newCommandQueue];

        // Check that the host and the GPU agree on the state layout
        const std::vector<uint64_t> layout = vl2metal::layout();
        id<MTLBuffer> layoutBuf = [dev newBufferWithLength:layout.size() * 8
                                                   options:MTLResourceStorageModeShared];
        id<MTLBuffer> oneTile = [dev newBufferWithLength:sizeof(vl2metal::Root)
                                                 options:MTLResourceStorageModeShared];
        {
            id<MTLComputePipelineState> ps = [dev
                newComputePipelineStateWithFunction:[lib newFunctionWithName:@"vl2metal_layout"]
                                              error:&err];
            id<MTLCommandBuffer> cb = [queue commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            [enc setComputePipelineState:ps];
            [enc setBuffer:layoutBuf offset:0 atIndex:0];
            [enc setBuffer:oneTile offset:0 atIndex:1];
            [enc dispatchThreads:MTLSizeMake(1, 1, 1) threadsPerThreadgroup:MTLSizeMake(1, 1, 1)];
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];
            const uint64_t* gpu = static_cast<const uint64_t*>(layoutBuf.contents);
            for (size_t i = 0; i < layout.size(); ++i) {
                if (gpu[i] != layout[i]) {
                    fprintf(stderr, "layout mismatch at member %zu: gpu %llu host %llu\n", i,
                            (unsigned long long)gpu[i], (unsigned long long)layout[i]);
                    return 1;
                }
            }
        }

        // Initialize: the first evaluation runs initial blocks and settles on the CPU
        const size_t tiles = (instances + vl2metal::kLanes - 1) / vl2metal::kLanes;
        const size_t bytes = tiles * sizeof(vl2metal::Root);
        id<MTLBuffer> state = [dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        if (!state) {
            fprintf(stderr, "cannot allocate %.2f GB\n", bytes / 1e9);
            return 1;
        }
        auto* roots = static_cast<vl2metal::Root*>(state.contents);
        t0 = now();
        std::vector<uint64_t> startEvals(instances, 1);
        if (stagger) {
            CpuModel init{seeds[0]};
            for (size_t i = 0; i < instances; ++i) {
                seeds[i] = seeds[0];
                while (init.evals < 1 + stagger * i && init.step()) {}
                startEvals[i] = init.evals;
                vl2metal_pack(roots, i, *init.model->rootp);
                setLaneRng(roots, i);
            }
        } else {
            CpuModel first{seeds[0]};
            first.step();
            vl2metal_pack(roots, 0, *first.model->rootp);
            setLaneRng(roots, 0);
            uint64_t after[2], fresh[2];
            threadRngState(after);
            first.ctx->randSeed(seeds[0]);
            threadRngState(fresh);
            const bool shared = !std::memcmp(after, fresh, sizeof(after));
            for (size_t i = 1; i < instances; ++i) {
                if (shared) {
                    vl2metal_pack(roots, i, *first.model->rootp);
                    first.ctx->randSeed(seeds[i]);
                    setLaneRng(roots, i);
                } else {
                    CpuModel init{seeds[i]};
                    init.step();
                    vl2metal_pack(roots, i, *init.model->rootp);
                    setLaneRng(roots, i);
                }
            }
            printf("initial state %s\n", shared ? "shared by all seeds (no random numbers drawn)"
                                                 : "computed per seed");
        }
        // Padding lanes of the last tile start finished
        for (size_t i = instances; i < tiles * vl2metal::kLanes; ++i)
            roots[i / vl2metal::kLanes].__Vgpu_finish[i % vl2metal::kLanes] = 1;
        printf("instances %zu, state %.2f GB, init %.2f s\n", instances, bytes / 1e9, now() - t0);

        // Run until every instance finished
        struct Args {
            uint32_t halfCycles;
            uint32_t instances;
        } args{half, static_cast<uint32_t>(instances)};
        const NSUInteger tg = std::min<NSUInteger>(tgSize, run.maxTotalThreadsPerThreadgroup);
        double gpuTime = 0;
        uint64_t done = 0;
        t0 = now();
        while (done < maxHalf) {
            id<MTLCommandBuffer> cb = [queue commandBuffer];
            id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
            [enc setComputePipelineState:run];
            [enc setBuffer:state offset:0 atIndex:0];
            [enc setBytes:&args length:sizeof(args) atIndex:1];
            for (uint32_t d = 0; d < perCb; ++d) {
                [enc dispatchThreads:MTLSizeMake(instances, 1, 1)
                    threadsPerThreadgroup:MTLSizeMake(tg, 1, 1)];
            }
            [enc endEncoding];
            [cb commit];
            [cb waitUntilCompleted];
            if (cb.error) {
                fprintf(stderr, "command buffer: %s\n", cb.error.localizedDescription.UTF8String);
                return 1;
            }
            gpuTime += cb.GPUEndTime - cb.GPUStartTime;
            done += uint64_t{half} * perCb;
            size_t finished = 0;
            for (size_t i = 0; i < instances; ++i) finished += vl2metal_finish(roots, i) != 0;
            if (finished == instances) break;
        }
        const double wall = now() - t0;

        // Results, and throughput from the evaluations each instance actually ran
        uint64_t evals = 0;
        size_t counts[4] = {};
        const char* const names[4] = {"PASS", "FAIL", "TIMEOUT", "ERROR"};
        std::vector<uint64_t> gpuEvals(instances);
        FILE* results = resultsFile.empty() ? nullptr : fopen(resultsFile.c_str(), "w");
        for (size_t i = 0; i < instances; ++i) {
            const vl2metal::Root& t = roots[i / vl2metal::kLanes];
            const size_t l = i % vl2metal::kLanes;
            gpuEvals[i] = t.__Vgpu_evals[l];
            evals += gpuEvals[i];
            const uint8_t finish = t.__Vgpu_finish[l];
            const int r = finish == 0 ? 2 : finish == 2 ? 3 : vl2metal_status(t, l) == passValue ? 0 : 1;
            ++counts[r];
            if (results)
                fprintf(results, "%d %s %llu\n", seeds[i], names[r],
                        (unsigned long long)(startEvals[i] + gpuEvals[i]) / 2);
        }
        if (results) fclose(results);
        printf("run: wall %.3f s, gpu %.3f s, %.2f M instance-cycles/s, %.2f instances/s, "
               "pass %zu, fail %zu, timeout %zu, error %zu\n",
               wall, gpuTime, evals / 2.0 / wall / 1e6, instances / wall, counts[0], counts[1],
               counts[2], counts[3]);

        // Replay instances on the CPU model and compare the whole state
        int bad = 0;
        for (size_t k = 0; k < std::min(verify, instances); ++k) {
            const size_t i = k == 0 ? 0 : instances - 1 - (k - 1) * (instances / verify);
            CpuModel ref{seeds[i]};
            while (ref.evals < startEvals[i] + gpuEvals[i] && ref.step()) {}
            std::vector<std::string> diffs = vl2metal_compare(roots, i, *ref.model->rootp);
            uint64_t rng[2];
            threadRngState(rng);
            const vl2metal::Root& t = roots[i / vl2metal::kLanes];
            if (rng[0] != t.__Vgpu_rng0[i % vl2metal::kLanes]
                || rng[1] != t.__Vgpu_rng1[i % vl2metal::kLanes])
                diffs.push_back("random generator");
            printf("verify instance %zu (seed %d): %llu evaluations, %s", i, seeds[i],
                   (unsigned long long)gpuEvals[i], diffs.empty() ? "state matches" : "DIFFERS:");
            for (size_t d = 0; d < std::min<size_t>(diffs.size(), 8); ++d)
                printf(" %s", diffs[d].c_str());
            printf("\n");
            bad += !diffs.empty();
        }
        return bad ? 2 : 0;
    }
}
