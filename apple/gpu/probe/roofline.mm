// Metal roofline probe for the M4 GPU: memory bandwidth, integer throughput, dispatch latency.
// Build: clang++ -O2 -std=c++17 -fobjc-arc roofline.mm -framework Metal -framework Foundation -o roofline
#import <Foundation/Foundation.h>
#import <Metal/Metal.h>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <vector>

static const char* kSource = R"MSL(
#include <metal_stdlib>
using namespace metal;

kernel void copy4(device const uint4* src [[buffer(0)]], device uint4* dst [[buffer(1)]],
                  uint i [[thread_position_in_grid]]) {
    dst[i] = src[i];
}

// 8 independent chains of xor/shift/add, like bit-level RTL logic; iters*8*3 ops per thread
kernel void intops(device uint* out [[buffer(0)]], constant uint& iters [[buffer(1)]],
                   uint i [[thread_position_in_grid]]) {
    uint a0 = i, a1 = i ^ 1, a2 = i ^ 2, a3 = i ^ 3, a4 = i ^ 4, a5 = i ^ 5, a6 = i ^ 6, a7 = i ^ 7;
    for (uint k = 0; k < iters; ++k) {
#define STEP(a) a = (a ^ (a << 3)) + (a >> 5);
        STEP(a0) STEP(a1) STEP(a2) STEP(a3) STEP(a4) STEP(a5) STEP(a6) STEP(a7)
    }
    out[i] = a0 ^ a1 ^ a2 ^ a3 ^ a4 ^ a5 ^ a6 ^ a7;
}

kernel void empty(device uint* out [[buffer(0)]], uint i [[thread_position_in_grid]]) {
    if (i == 0xffffffffu) out[0] = 0;
}
)MSL";

static double now() {
    return std::chrono::duration<double>(std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

static id<MTLComputePipelineState> pipeline(id<MTLDevice> dev, id<MTLLibrary> lib,
                                            const char* name) {
    NSError* err = nil;
    id<MTLFunction> fn = [lib newFunctionWithName:[NSString stringWithUTF8String:name]];
    id<MTLComputePipelineState> ps = [dev newComputePipelineStateWithFunction:fn error:&err];
    if (!ps) {
        fprintf(stderr, "pipeline %s: %s\n", name, err.localizedDescription.UTF8String);
        exit(1);
    }
    return ps;
}

// Run one dispatch of n threads, return GPU seconds
template <typename F>
static double dispatch(id<MTLCommandQueue> q, id<MTLComputePipelineState> ps, NSUInteger n,
                       F bind) {
    id<MTLCommandBuffer> cb = [q commandBuffer];
    id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
    [enc setComputePipelineState:ps];
    bind(enc);
    NSUInteger tg = std::min<NSUInteger>(ps.maxTotalThreadsPerThreadgroup, 256);
    [enc dispatchThreads:MTLSizeMake(n, 1, 1) threadsPerThreadgroup:MTLSizeMake(tg, 1, 1)];
    [enc endEncoding];
    [cb commit];
    [cb waitUntilCompleted];
    return cb.GPUEndTime - cb.GPUStartTime;
}

int main() {
    @autoreleasepool {
        id<MTLDevice> dev = MTLCreateSystemDefaultDevice();
        printf("device: %s, recommendedMaxWorkingSetSize %.1f GB\n", dev.name.UTF8String,
               dev.recommendedMaxWorkingSetSize / 1e9);
        NSError* err = nil;
        id<MTLLibrary> lib = [dev newLibraryWithSource:@(kSource) options:nil error:&err];
        if (!lib) {
            fprintf(stderr, "compile: %s\n", err.localizedDescription.UTF8String);
            return 1;
        }
        id<MTLCommandQueue> q = [dev newCommandQueue];

        // Bandwidth: copy 1 GB, best of 5
        const size_t bytes = size_t{1} << 30;
        id<MTLBuffer> a = [dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        id<MTLBuffer> b = [dev newBufferWithLength:bytes options:MTLResourceStorageModeShared];
        memset(a.contents, 1, bytes);
        memset(b.contents, 0, bytes);
        auto copy4 = pipeline(dev, lib, "copy4");
        double best = 1e9;
        for (int r = 0; r < 5; ++r) {
            best = std::min(best, dispatch(q, copy4, bytes / 16, [&](auto enc) {
                [enc setBuffer:a offset:0 atIndex:0];
                [enc setBuffer:b offset:0 atIndex:1];
            }));
        }
        printf("copy bandwidth: %.1f GB/s (read+write)\n", 2.0 * bytes / best / 1e9);

        // Integer throughput vs thread count: shows how many threads fill the GPU
        auto intops = pipeline(dev, lib, "intops");
        id<MTLBuffer> out = [dev newBufferWithLength:(1 << 22) * 4
                                             options:MTLResourceStorageModeShared];
        const uint32_t iters = 4096;
        printf("intops (3 ops x 8 chains x %u iters per thread):\n", iters);
        for (NSUInteger n = 64; n <= (1 << 20); n *= 2) {
            double t = 1e9;
            for (int r = 0; r < 3; ++r) {
                t = std::min(t, dispatch(q, intops, n, [&](auto enc) {
                    [enc setBuffer:out offset:0 atIndex:0];
                    [enc setBytes:&iters length:sizeof(iters) atIndex:1];
                }));
            }
            printf("  threads %8lu: %8.1f Gops/s  %.3f ms\n", (unsigned long)n,
                   3.0 * 8 * iters * n / t / 1e9, t * 1e3);
        }

        // Dispatch round trip: commit + wait of an empty kernel, CPU wall clock
        auto empty = pipeline(dev, lib, "empty");
        std::vector<double> lat;
        for (int r = 0; r < 200; ++r) {
            const double t0 = now();
            dispatch(q, empty, 1024, [&](auto enc) { [enc setBuffer:out offset:0 atIndex:0]; });
            lat.push_back(now() - t0);
        }
        std::sort(lat.begin(), lat.end());
        printf("dispatch round trip: median %.1f us, p10 %.1f us\n", lat[100] * 1e6,
               lat[20] * 1e6);

        // Many dispatches in one command buffer: per-dispatch cost when batched
        const int nd = 1000;
        id<MTLCommandBuffer> cb = [q commandBuffer];
        id<MTLComputeCommandEncoder> enc = [cb computeCommandEncoder];
        [enc setComputePipelineState:empty];
        [enc setBuffer:out offset:0 atIndex:0];
        for (int r = 0; r < nd; ++r) {
            [enc dispatchThreads:MTLSizeMake(1024, 1, 1) threadsPerThreadgroup:MTLSizeMake(256, 1, 1)];
        }
        [enc endEncoding];
        const double t0 = now();
        [cb commit];
        [cb waitUntilCompleted];
        printf("%d dispatches in one encoder: %.2f us each (GPU %.2f us each)\n", nd,
               (now() - t0) / nd * 1e6, (cb.GPUEndTime - cb.GPUStartTime) / nd * 1e6);
    }
    return 0;
}
