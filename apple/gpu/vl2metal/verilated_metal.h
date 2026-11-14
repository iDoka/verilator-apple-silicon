// Metal Shading Language subset of the Verilator runtime, for models translated by vl2metal.py.
// Only what the translated per-cycle code uses. Ports of include/verilated_funcs.h keep the CPU
// semantics bit for bit, including dirty upper bits, so GPU and CPU states compare equal.
#pragma once
#include <metal_stdlib>
using namespace metal;

typedef uchar CData;
typedef ushort SData;
typedef uint IData;
typedef ulong QData;
typedef uint EData;

#define VL_ATTR_UNUSED
#define VL_ATTR_COLD
#define VL_UNLIKELY(x) (x)
#define VL_LIKELY(x) (x)
#define VL_EDATASIZE 32
#define VL_SIZEBITS_E 31
#define VL_WORDS_I(nbits) (((nbits) + 31) >> 5)
#define VL_MASK_I(nbits) (((nbits) & 31) ? ((1U << ((nbits) & 31)) - 1) : ~0U)
#define VL_MASK_E(nbits) VL_MASK_I(nbits)
#define VL_MASK_Q(nbits) (((nbits) & 63) ? ((1UL << ((nbits) & 63)) - 1UL) : ~0UL)
#define VL_BITWORD_E(bit) ((bit) >> 5)
#define VL_BITBIT_E(bit) ((bit) & 31)
#define VL_SIGN_E(nbits, lhs) ((lhs) >> VL_BITBIT_E((nbits) - 1U))
#define VL_CLEAN_II(obits, lbits, lhs) ((lhs) & (VL_MASK_I(obits)))

// Element access in every address space the translated code uses
#define VL_METAL_INDEX_OPS(T) \
    thread T& operator[](uint i) thread { return m_storage[i]; } \
    device T& operator[](uint i) device { return m_storage[i]; } \
    const thread T& operator[](uint i) const thread { return m_storage[i]; } \
    const device T& operator[](uint i) const device { return m_storage[i]; } \
    constant const T& operator[](uint i) constant { return m_storage[i]; }

template <int N>
struct VlWide {
    EData m_storage[N];
    VL_METAL_INDEX_OPS(EData)
};

template <class T, int N>
struct VlUnpacked {
    T m_storage[N];
    VL_METAL_INDEX_OPS(T)
};

//======================================================================
// Scalar helpers

inline IData VL_REDXOR_2(IData r) { return r ^ (r >> 1); }  // CPU version is not a builtin
inline IData VL_REDXOR_4(IData r) { return popcount(r) & 1; }
inline IData VL_REDXOR_8(IData r) { return popcount(r) & 1; }
inline IData VL_REDXOR_16(IData r) { return popcount(r) & 1; }
inline IData VL_REDXOR_32(IData r) { return popcount(r) & 1; }
inline IData VL_REDXOR_64(QData r) { return static_cast<IData>(popcount(r) & 1); }
inline IData VL_COUNTONES_I(IData lhs) { return popcount(lhs); }

inline IData VL_SHIFTRS_III(int obits, int lbits, int, IData lhs, IData rhs) {
    const IData sign = -(lhs >> (lbits - 1));
    if (rhs >= 32) return sign & VL_MASK_I(obits);
    const IData signext = ~(VL_MASK_I(lbits) >> rhs);
    return (lhs >> rhs) | (sign & VL_CLEAN_II(obits, obits, signext));
}

template <class M>
inline IData VL_MATCHMASKED_I(int, IData lhs, M matchp) {
    uint i = 0;
    while (true) {
        const IData mask = matchp[i * 2];
        const IData bits = matchp[i * 2 + 1];
        if ((mask & lhs) == bits) break;
        ++i;
    }
    return i;
}

//======================================================================
// Wide helpers on thread memory, ported from verilated_funcs.h

inline void vl_zero_w(int obits, thread EData* owp) {
    for (int i = 0; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
}

inline void vl_insert_ww(thread EData* iowp, const thread EData* lwp, int hbit, int lbit,
                         int rbits) {
    const int hoffset = VL_BITBIT_E(hbit);
    const int loffset = VL_BITBIT_E(lbit);
    const int roffset = VL_BITBIT_E(rbits);
    const int lword = VL_BITWORD_E(lbit);
    const int hword = VL_BITWORD_E(hbit);
    const int rword = VL_BITWORD_E(rbits);
    const int words = VL_WORDS_I(hbit - lbit + 1);
    const EData cleanmask = hword == rword ? VL_MASK_E(roffset) : VL_MASK_E(0);
    if (hoffset == VL_SIZEBITS_E && loffset == 0) {
        for (int i = 0; i < (words - 1); ++i) iowp[lword + i] = lwp[i];
        iowp[hword] = lwp[words - 1] & cleanmask;
    } else if (loffset == 0) {
        for (int i = 0; i < (words - 1); ++i) iowp[lword + i] = lwp[i];
        const EData hinsmask = (VL_MASK_E(hoffset - 0 + 1));
        iowp[hword] = (iowp[hword] & ~hinsmask) | (lwp[words - 1] & (hinsmask & cleanmask));
    } else {
        const EData hinsmask = (VL_MASK_E(hoffset - 0 + 1)) << 0;
        const EData linsmask = (VL_MASK_E((VL_EDATASIZE - 1) - loffset + 1)) << loffset;
        const int nbitsonright = VL_EDATASIZE - loffset;
        for (int i = 0; i < words; ++i) {
            {
                const int oword = lword + i;
                const EData d = lwp[i] << loffset;
                const EData od = (iowp[oword] & ~linsmask) | (d & linsmask);
                if (oword == hword) {
                    iowp[oword] = (iowp[oword] & ~hinsmask) | (od & (hinsmask & cleanmask));
                } else {
                    iowp[oword] = od;
                }
            }
            {
                const int oword = lword + i + 1;
                if (oword <= hword) {
                    const EData d = lwp[i] >> nbitsonright;
                    const EData od = (d & ~linsmask) | (iowp[oword] & linsmask);
                    if (oword == hword) {
                        iowp[oword] = (iowp[oword] & ~hinsmask) | (od & (hinsmask & cleanmask));
                    } else {
                        iowp[oword] = od;
                    }
                }
            }
        }
    }
}

inline void vl_shiftl_wwi(int obits, thread EData* owp, const thread EData* lwp, IData rd) {
    const int word_shift = VL_BITWORD_E(rd);
    const int bit_shift = VL_BITBIT_E(rd);
    if (rd >= static_cast<IData>(obits)) {
        for (int i = 0; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
    } else if (bit_shift == 0) {
        for (int i = 0; i < word_shift; ++i) owp[i] = 0;
        for (int i = word_shift; i < VL_WORDS_I(obits); ++i) owp[i] = lwp[i - word_shift];
    } else {
        for (int i = 0; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
        vl_insert_ww(owp, lwp, obits - 1, rd, 0);
    }
}

inline void vl_shiftr_wwi(int obits, thread EData* owp, const thread EData* lwp, IData rd) {
    const int word_shift = VL_BITWORD_E(rd);
    const int bit_shift = VL_BITBIT_E(rd);
    if (rd >= static_cast<IData>(obits)) {
        for (int i = 0; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
    } else if (bit_shift == 0) {
        const int copy_words = (VL_WORDS_I(obits) - word_shift);
        for (int i = 0; i < copy_words; ++i) owp[i] = lwp[i + word_shift];
        for (int i = copy_words; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
    } else {
        const int loffset = rd & VL_SIZEBITS_E;
        const int nbitsonright = VL_EDATASIZE - loffset;
        const int words = VL_WORDS_I(obits - rd);
        for (int i = 0; i < words; ++i) {
            owp[i] = lwp[i + word_shift] >> loffset;
            const int upperword = i + word_shift + 1;
            if (upperword < VL_WORDS_I(obits)) owp[i] |= lwp[upperword] << nbitsonright;
        }
        for (int i = words; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
    }
}

inline void vl_extends_wq(int obits, int lbits, thread EData* owp, QData ld) {
    owp[0] = static_cast<EData>(ld);
    owp[1] = static_cast<EData>(ld >> 32);
    if (VL_SIGN_E(lbits, owp[1])) {
        owp[1] |= ~VL_MASK_E(lbits);
        for (int i = 2; i < VL_WORDS_I(obits); ++i) owp[i] = ~0U;
    } else {
        for (int i = 2; i < VL_WORDS_I(obits); ++i) owp[i] = 0;
    }
}

inline void vl_negate_w(int words, thread EData* owp, const thread EData* lwp) {
    EData carry = 1;
    for (int i = 0; i < words; ++i) {
        owp[i] = ~lwp[i] + carry;
        carry = (owp[i] < ~lwp[i]);
    }
}

inline void vl_mul_w(int words, thread EData* owp, const thread EData* lwp,
                     const thread EData* rwp) {
    for (int i = 0; i < words; ++i) owp[i] = 0;
    for (int lword = 0; lword < words; ++lword) {
        for (int rword = 0; rword < words; ++rword) {
            QData mul = static_cast<QData>(lwp[lword]) * static_cast<QData>(rwp[rword]);
            for (int qword = lword + rword; qword < words; ++qword) {
                mul += static_cast<QData>(owp[qword]);
                owp[qword] = (mul & 0xffffffffUL);
                mul = (mul >> 32UL) & 0xffffffffUL;
            }
        }
    }
}

#define VL_METAL_MULS_MAX_WORDS 8
inline void vl_muls_www(int lbits, thread EData* owp, const thread EData* lwp,
                        const thread EData* rwp) {
    const int words = VL_WORDS_I(lbits);
    EData lwstore[VL_METAL_MULS_MAX_WORDS];
    EData rwstore[VL_METAL_MULS_MAX_WORDS];
    const EData lneg = VL_SIGN_E(lbits, lwp[words - 1]);
    if (lneg) {
        vl_negate_w(words, lwstore, lwp);
        lwstore[words - 1] &= VL_MASK_E(lbits);
        lwp = lwstore;
    }
    const EData rneg = VL_SIGN_E(lbits, rwp[words - 1]);
    if (rneg) {
        vl_negate_w(words, rwstore, rwp);
        rwstore[words - 1] &= VL_MASK_E(lbits);
        rwp = rwstore;
    }
    vl_mul_w(words, owp, lwp, rwp);
    owp[words - 1] &= VL_MASK_E(lbits);
    if ((lneg ^ rneg) & 1) {
        QData carry = 0;
        for (int i = 0; i < words; ++i) {
            carry = carry + static_cast<QData>(static_cast<IData>(~owp[i]));
            if (i == 0) ++carry;
            owp[i] = (carry & 0xffffffffUL);
            carry = (carry >> 32UL) & 0xffffffffUL;
        }
    }
}

//======================================================================
// Wide helper entry points: inputs are copied to thread memory, outputs may live in thread
// (temporaries) or device (model state) memory.

template <class O>
inline void VL_ZERO_W(int obits, thread O& o) { vl_zero_w(obits, o.m_storage); }
template <class O>
inline void VL_ZERO_W(int obits, device O& o) {
    O t = o;
    vl_zero_w(obits, t.m_storage);
    o = t;
}

template <class O, class I>
inline void VL_SHIFTR_WWI(int obits, int, int, thread O& o, I l, IData rd) {
    vl_shiftr_wwi(obits, o.m_storage, l.m_storage, rd);
}
template <class O, class I>
inline void VL_SHIFTR_WWI(int obits, int, int, device O& o, I l, IData rd) {
    O t = o;
    vl_shiftr_wwi(obits, t.m_storage, l.m_storage, rd);
    o = t;
}

template <class O, class I>
inline void VL_SHIFTL_WWI(int obits, int, int, thread O& o, I l, IData rd) {
    vl_shiftl_wwi(obits, o.m_storage, l.m_storage, rd);
}
template <class O, class I>
inline void VL_SHIFTL_WWI(int obits, int, int, device O& o, I l, IData rd) {
    O t = o;
    vl_shiftl_wwi(obits, t.m_storage, l.m_storage, rd);
    o = t;
}

template <class O>
inline void VL_EXTENDS_WQ(int obits, int lbits, thread O& o, QData ld) {
    vl_extends_wq(obits, lbits, o.m_storage, ld);
}
template <class O>
inline void VL_EXTENDS_WQ(int obits, int lbits, device O& o, QData ld) {
    O t = o;
    vl_extends_wq(obits, lbits, t.m_storage, ld);
    o = t;
}

template <class O, class L, class R>
inline void VL_MULS_WWW(int lbits, thread O& o, L l, R r) {
    vl_muls_www(lbits, o.m_storage, l.m_storage, r.m_storage);
}
template <class O, class L, class R>
inline void VL_MULS_WWW(int lbits, device O& o, L l, R r) {
    O t = o;
    vl_muls_www(lbits, t.m_storage, l.m_storage, r.m_storage);
    o = t;
}

//======================================================================
// Random numbers: port of VlRNG (xoroshiro128**, include/verilated.cpp). The CPU model draws
// from a thread-local generator; on the GPU each instance has its own state in the members
// __Vgpu_rng0/1, which vl2metal passes as the first two arguments (VL_GPU_RNG).

#define VL_GPU_RNG vlSelf->__Vgpu_rng0[L], vlSelf->__Vgpu_rng1[L]
#define VL_RNG_PARAMS device QData &s0r, device QData &s1r

inline QData vl_rolt(QData x, int k) { return (x << k) | (x >> (64 - k)); }

inline QData vl_rand64(VL_RNG_PARAMS) {
    const QData s0 = s0r;
    const QData s1 = s1r ^ s0;
    s0r = vl_rolt(s0, 24) ^ s1 ^ (s1 << 16);
    s1r = vl_rolt(s1, 37);
    return vl_rolt(s0 * 5, 7) * 9;
}

inline QData vl_splitmix64(thread QData& x) {
    QData z = (x += 0x9e3779b97f4a7c15UL);
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9UL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebUL;
    return z ^ (z >> 31);
}

// VerilatedContext::randSeed(seed) then reseeding the thread generator. A seed of 0 picks a
// time-based seed on the CPU, which cannot be reproduced; the GPU uses 0.
inline void vl_rng_reseed(VL_RNG_PARAMS, IData seed) {
    const QData s = static_cast<QData>(static_cast<long>(static_cast<int>(seed)));
    QData x = (s << 32) ^ s;
    s0r = vl_splitmix64(x);
    s1r = vl_splitmix64(x);
}

inline IData VL_RANDOM_I(VL_RNG_PARAMS) { return static_cast<IData>(vl_rand64(s0r, s1r)); }
inline QData VL_RANDOM_Q(VL_RNG_PARAMS) { return vl_rand64(s0r, s1r); }
template <class O>
inline void VL_RANDOM_W(VL_RNG_PARAMS, int obits, thread O& o) {
    for (int i = 0; i < VL_WORDS_I(obits); ++i) o[i] = static_cast<EData>(vl_rand64(s0r, s1r));
}
template <class O>
inline void VL_RANDOM_W(VL_RNG_PARAMS, int obits, device O& o) {
    for (int i = 0; i < VL_WORDS_I(obits); ++i) o[i] = static_cast<EData>(vl_rand64(s0r, s1r));
}
inline IData VL_RANDOM_SEEDED_II(VL_RNG_PARAMS, device IData& seedr) {
    vl_rng_reseed(s0r, s1r, seedr);
    seedr = VL_RANDOM_I(s0r, s1r);
    return VL_RANDOM_I(s0r, s1r);
}
inline IData VL_RANDOM_SEEDED_II(VL_RNG_PARAMS, thread IData& seedr) {
    vl_rng_reseed(s0r, s1r, seedr);
    seedr = VL_RANDOM_I(s0r, s1r);
    return VL_RANDOM_I(s0r, s1r);
}
inline IData VL_URANDOM_SEEDED_II(VL_RNG_PARAMS, IData seed) {
    vl_rng_reseed(s0r, s1r, seed);
    return VL_RANDOM_I(s0r, s1r);
}
inline IData VL_URANDOM_RANGE_I(VL_RNG_PARAMS, IData hi, IData lo) {
    const QData rnd = vl_rand64(s0r, s1r);
    if (hi > lo) {
        if (hi - lo + 1 == 0) return rnd;
        return (rnd % (hi - lo + 1)) + lo;
    }
    if (lo - hi + 1 == 0) return rnd;
    return (rnd % (lo - hi + 1)) + hi;
}
