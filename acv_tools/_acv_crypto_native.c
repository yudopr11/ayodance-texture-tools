/* Optional native C99 crypto accelerator for acv_tools.acv_crypto.
 *
 * Implements the exact legacy ACV archive ciphers:
 *   - Variant 3 / 5: deterministic MT19937 permutation + ArchiveBlowfish ECB
 *   - Variant 6:     Rijndael-256 (32-byte block, 256-bit key) CBC
 *
 * Pure, stateless per-call routines (after one-time acv_crypto_init()) so
 * ctypes.CDLL calls release the Python GIL and scale cleanly across threads.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef _WIN32
#define ACV_EXPORT __declspec(dllexport)
#else
#define ACV_EXPORT __attribute__((visibility("default")))
#endif

static inline uint32_t load_be32(const uint8_t *p) {
    return ((uint32_t)p[0] << 24) | ((uint32_t)p[1] << 16) |
           ((uint32_t)p[2] << 8) | (uint32_t)p[3];
}

static inline void store_be32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)(v >> 24);
    p[1] = (uint8_t)(v >> 16);
    p[2] = (uint8_t)(v >> 8);
    p[3] = (uint8_t)v;
}

/* ========================================================================= */
/* ArchiveBlowfish (MT19937 + sparse native prime table + custom Blowfish)   */
/* ========================================================================= */

typedef struct {
    uint32_t words[624];
    uint32_t index;
} acv_mt19937_t;

static void mt_init(acv_mt19937_t *mt, uint32_t seed) {
    mt->words[0] = seed;
    for (uint32_t i = 1; i < 624; ++i) {
        uint32_t prev = mt->words[i - 1];
        mt->words[i] = (prev ^ (prev >> 30)) * 1812433253u + i;
    }
    mt->index = 624;
}

static uint32_t mt_next(acv_mt19937_t *mt) {
    if (mt->index == 624) {
        for (uint32_t i = 0; i < 624; ++i) {
            uint32_t val = (mt->words[i] & 0x80000000u) |
                           (mt->words[(i + 1) % 624] & 0x7fffffffu);
            mt->words[i] = mt->words[(i + 397) % 624] ^ (val >> 1) ^
                           ((val & 1u) ? 0x9908b0dfu : 0u);
        }
        mt->index = 0;
    }
    uint32_t v = mt->words[mt->index++];
    v ^= v >> 11;
    v ^= (v << 7) & 0x9d2c5680u;
    v ^= (v << 15) & 0xefc60000u;
    return v ^ (v >> 18);
}

static uint32_t mt_between(acv_mt19937_t *mt, uint32_t low, uint32_t high) {
    return low + mt_next(mt) % (high - low + 1u);
}

/* Exact sparse native permutation prime table matching acv_crypto.PRIMES. */
static const uint32_t ACV_PRIMES[] = {
    2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37,
    41, 43, 47, 53, 59, 61, 67, 71, 73, 79, 83, 89,
    97, 101, 103, 107, 109, 113, 127, 131, 137, 139, 149, 151,
    157, 163, 167, 173, 179, 181, 191, 193, 197, 199, 211, 223,
    227, 229, 233, 239, 241, 251, 257, 263, 269, 271, 277, 281,
    283, 293, 307, 311, 313, 317, 331, 337, 347, 349, 353, 359,
    367, 373, 379, 383, 389, 397, 401, 409, 419, 421, 431, 433,
    439, 443, 449, 457, 461, 463, 467, 479, 487, 491, 499, 503,
    509, 521, 523, 541, 547, 557, 563, 569, 571, 577, 587, 593,
    599, 601, 607, 613, 617, 619, 631, 641, 643, 647, 653, 659,
    661, 673, 677, 683, 691, 701, 709, 719, 727, 733, 739, 743,
    751, 757, 761, 769, 773, 787, 797, 809, 811, 821, 823, 827,
    829, 839, 853, 857, 859, 863, 877, 881, 883, 887, 907, 911,
    919, 929, 937, 941, 947, 953, 967, 971, 977, 983, 991, 997,
    1009, 1051, 1103, 1151, 1201, 1259, 1301, 1361, 1409, 1451, 1511, 1553,
    1601, 1657, 1709, 1753, 1801, 1861, 1901, 1951, 2003, 2053, 2111, 2113,
    2153, 2203, 2251, 2309, 2351, 2411, 2459, 2503, 2551, 2557, 2609, 2657,
    2707, 2753, 2767, 2801, 2851, 2903, 2953, 3001, 3061, 3109, 3163, 3203,
    3251, 3301, 3359, 3407, 3457, 3511, 3557, 3607, 3659, 3701, 3761, 3803,
    3851, 3907, 3967, 4001, 4051, 4111, 4153, 4201, 4253, 4327, 4357, 4409,
    4451, 4507, 4561, 4603, 4651, 4703, 4751, 4801, 4861, 4903, 4951, 5003,
    5101, 5209, 5303, 5407, 5501, 5623, 5701, 5801, 5903, 6007, 6101, 6211,
    6301, 6421, 6521, 6607, 6701, 6803, 6907, 7001, 7103, 7207, 7307, 7411,
    7507, 7603, 7703, 7817, 7901, 8009, 8101, 8209, 8311, 8419, 8501, 8609,
    8707, 8803, 8923, 9001, 9103, 9203, 9311, 9403, 9511, 9601, 9719, 9803,
    9901, 10007, 10501, 11003, 11503, 12007, 12503, 13001, 13513, 14009, 14503, 15013,
    15511, 16033, 16519, 17011, 17509, 18013, 18503, 19001, 19501, 20011, 20507, 21001,
    21503, 22003, 22501, 23003, 23509, 24001, 24509, 25013, 26003, 27011, 28001, 29009,
    30011, 31013, 31991, 32999, 33997, 34981, 35999, 36997, 37997, 38993, 39989, 40993,
    41999, 42989, 43997, 44987, 45989, 46997, 47981, 48991, 49999, 59999, 69997, 79999,
    89989, 99991, 109987, 119993, 129971, 139999, 149993, 159979, 169991, 179999, 189997, 199999,
    209987, 219983, 229981, 239999, 249989, 259993, 269987, 279991, 289999, 299993, 309989, 319993,
    329999, 339991, 349981
};

static void fill_permuted_table(acv_mt19937_t *outer, uint32_t *values,
                                uint32_t size,
                                uint32_t r0_lo, uint32_t r0_hi,
                                uint32_t r1_lo, uint32_t r1_hi,
                                uint32_t r2_lo, uint32_t r2_hi,
                                uint32_t limit, uint32_t offset) {
    uint32_t u0 = mt_between(outer, r0_lo, r0_hi);
    uint32_t u1 = mt_between(outer, r1_lo, r1_hi);
    uint32_t u2 = mt_between(outer, r2_lo, r2_hi);
    uint32_t i0 = mt_between(outer, 0, u0);
    uint32_t i1 = mt_between(outer, 0, u1);
    uint32_t i2 = mt_between(outer, 0, u2);

    acv_mt19937_t perm_rng;
    mt_init(&perm_rng, size);
    uint64_t a = mt_between(&perm_rng, 0, ACV_PRIMES[i0]);
    uint64_t b = mt_between(&perm_rng, 0, ACV_PRIMES[i1]);
    uint64_t c = mt_between(&perm_rng, 0, ACV_PRIMES[i2]);
    uint32_t step = (uint32_t)((c + 1u + ((a + 1u) * (uint64_t)size + 1u + b) * (uint64_t)size) & 0x3fffffffu);

    uint32_t modulus = 0;
    size_t prime_count = sizeof(ACV_PRIMES) / sizeof(ACV_PRIMES[0]);
    for (size_t k = 0; k < prime_count; ++k) {
        if (ACV_PRIMES[k] >= size) {
            modulus = ACV_PRIMES[k];
            break;
        }
    }
    if (step % modulus == 0) {
        step += 1u;
    }

    uint32_t position = 0;
    for (uint32_t step_idx = 0; step_idx < modulus; ++step_idx) {
        position = (step + position) % modulus;
        if (position < size) {
            values[position] = (mt_next(outer) % limit) + offset;
        }
    }
}

#define BF_F(s0, s1, s2, s3, x) \
    ((((s0)[((x) >> 24) & 0xffu] + (s1)[((x) >> 16) & 0xffu]) ^ \
      (s2)[((x) >> 8) & 0xffu]) + (s3)[(x) & 0xffu])

#define BF_ROUND(l, r, rk) \
    do { \
        (l) ^= (rk); \
        (r) ^= BF_F(s0, s1, s2, s3, (l)); \
    } while (0)

static inline void bf_block(uint32_t *left_io, uint32_t *right_io,
                            const uint32_t *rounds, const uint32_t *s) {
    const uint32_t *s0 = s;
    const uint32_t *s1 = s + 256;
    const uint32_t *s2 = s + 512;
    const uint32_t *s3 = s + 768;
    uint32_t l = *left_io;
    uint32_t r = *right_io;

    BF_ROUND(l, r, rounds[0]);
    BF_ROUND(r, l, rounds[1]);
    BF_ROUND(l, r, rounds[2]);
    BF_ROUND(r, l, rounds[3]);
    BF_ROUND(l, r, rounds[4]);
    BF_ROUND(r, l, rounds[5]);
    BF_ROUND(l, r, rounds[6]);
    BF_ROUND(r, l, rounds[7]);
    BF_ROUND(l, r, rounds[8]);
    BF_ROUND(r, l, rounds[9]);
    BF_ROUND(l, r, rounds[10]);
    BF_ROUND(r, l, rounds[11]);
    BF_ROUND(l, r, rounds[12]);
    BF_ROUND(r, l, rounds[13]);
    BF_ROUND(l, r, rounds[14]);
    BF_ROUND(r, l, rounds[15]);

    *left_io = r ^ rounds[17];
    *right_io = l ^ rounds[16];
}

ACV_EXPORT int acv_blowfish_init(uint32_t tag, const uint8_t *key, size_t key_len,
                                 uint32_t p_enc[18], uint32_t p_dec[18], uint32_t s[1024]) {
    if (!key || key_len < 1 || key_len > 56 || !p_enc || !p_dec || !s) {
        return -1;
    }
    acv_mt19937_t rng;
    mt_init(&rng, tag ^ 0x49444eu);
    fill_permuted_table(&rng, p_enc, 18, 13, 17, 6, 12, 1, 5, 0xe8ddf945u, 0x03707344u);
    fill_permuted_table(&rng, s, 1024, 200, 300, 60, 150, 5, 30, 0xff0ef701u, 0x00250e2du);

    size_t kpos = 0;
    for (int i = 0; i < 18; ++i) {
        uint32_t part = 0;
        for (int b = 0; b < 4; ++b) {
            part = (part << 8) | key[kpos];
            if (++kpos == key_len) {
                kpos = 0;
            }
        }
        p_enc[i] ^= part;
    }

    uint32_t left = 0, right = 0;
    for (int i = 0; i < 18; i += 2) {
        bf_block(&left, &right, p_enc, s);
        p_enc[i] = left;
        p_enc[i + 1] = right;
    }
    for (int i = 0; i < 1024; i += 2) {
        bf_block(&left, &right, p_enc, s);
        s[i] = left;
        s[i + 1] = right;
    }
    for (int i = 0; i < 18; ++i) {
        p_dec[i] = p_enc[17 - i];
    }
    return 0;
}

ACV_EXPORT int acv_blowfish_ecb(const uint32_t rounds[18], const uint32_t s[1024],
                                const uint8_t *src, uint8_t *dst, size_t len) {
    if ((len & 7u) != 0) {
        return -1;
    }
    if (len == 0) {
        return 0;
    }
    if (!rounds || !s || !src || !dst) {
        return -1;
    }
    for (size_t offset = 0; offset < len; offset += 8) {
        uint32_t left = load_be32(src + offset);
        uint32_t right = load_be32(src + offset + 4);
        bf_block(&left, &right, rounds, s);
        store_be32(dst + offset, left);
        store_be32(dst + offset + 4, right);
    }
    return 0;
}

/* ========================================================================= */
/* Rijndael-256 (32-byte block, 256-bit key, 14 rounds) T-table CBC          */
/* ========================================================================= */

static uint8_t RJ_S[256];
static uint8_t RJ_Si[256];
static uint32_t RJ_T1[256], RJ_T2[256], RJ_T3[256], RJ_T4[256];
static uint32_t RJ_T5[256], RJ_T6[256], RJ_T7[256], RJ_T8[256];
static uint32_t RJ_U1[256], RJ_U2[256], RJ_U3[256], RJ_U4[256];
static const uint8_t RJ_RCON[16] = {
    0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80,
    0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d, 0x9a, 0x2f
};
static int rj_initialized = 0;

static uint8_t gf_xtime(uint8_t x) {
    return (uint8_t)((x << 1) ^ ((x & 0x80u) ? 0x1bu : 0x00u));
}

static uint8_t gf_mul(uint8_t a, uint8_t b) {
    uint8_t result = 0;
    for (int i = 0; i < 8; ++i) {
        if (b & 1u) {
            result ^= a;
        }
        a = gf_xtime(a);
        b >>= 1;
    }
    return result;
}

static uint8_t gf_inv(uint8_t x) {
    if (x == 0) {
        return 0;
    }
    /* x^254 in GF(2^8) */
    uint8_t acc = 1;
    uint8_t base = x;
    uint8_t exp = 254;
    while (exp) {
        if (exp & 1u) {
            acc = gf_mul(acc, base);
        }
        base = gf_mul(base, base);
        exp >>= 1;
    }
    return acc;
}

static uint8_t rotl8(uint8_t x, unsigned n) {
    return (uint8_t)((x << n) | (x >> (8u - n)));
}

ACV_EXPORT uint32_t acv_crypto_abi_version(void) {
    return 1u;
}

ACV_EXPORT void acv_crypto_init(void) {
    if (rj_initialized) {
        return;
    }
    for (int i = 0; i < 256; ++i) {
        uint8_t inv = gf_inv((uint8_t)i);
        uint8_t s = (uint8_t)(inv ^ rotl8(inv, 1) ^ rotl8(inv, 2) ^
                              rotl8(inv, 3) ^ rotl8(inv, 4) ^ 0x63u);
        RJ_S[i] = s;
        RJ_Si[s] = (uint8_t)i;
    }
    for (int i = 0; i < 256; ++i) {
        uint8_t s = RJ_S[i];
        uint8_t s2 = gf_xtime(s);
        uint8_t s3 = (uint8_t)(s2 ^ s);
        RJ_T1[i] = ((uint32_t)s2 << 24) | ((uint32_t)s << 16) | ((uint32_t)s << 8) | (uint32_t)s3;
        RJ_T2[i] = ((uint32_t)s3 << 24) | ((uint32_t)s2 << 16) | ((uint32_t)s << 8) | (uint32_t)s;
        RJ_T3[i] = ((uint32_t)s << 24) | ((uint32_t)s3 << 16) | ((uint32_t)s2 << 8) | (uint32_t)s;
        RJ_T4[i] = ((uint32_t)s << 24) | ((uint32_t)s << 16) | ((uint32_t)s3 << 8) | (uint32_t)s2;

        uint8_t x = (uint8_t)i;
        uint8_t e = gf_mul(x, 0x0eu);
        uint8_t n9 = gf_mul(x, 0x09u);
        uint8_t d = gf_mul(x, 0x0du);
        uint8_t b = gf_mul(x, 0x0bu);
        RJ_U1[i] = ((uint32_t)e << 24) | ((uint32_t)n9 << 16) | ((uint32_t)d << 8) | (uint32_t)b;
        RJ_U2[i] = ((uint32_t)b << 24) | ((uint32_t)e << 16) | ((uint32_t)n9 << 8) | (uint32_t)d;
        RJ_U3[i] = ((uint32_t)d << 24) | ((uint32_t)b << 16) | ((uint32_t)e << 8) | (uint32_t)n9;
        RJ_U4[i] = ((uint32_t)n9 << 24) | ((uint32_t)d << 16) | ((uint32_t)b << 8) | (uint32_t)e;
    }
    for (int i = 0; i < 256; ++i) {
        uint8_t si = RJ_Si[i];
        RJ_T5[i] = RJ_U1[si];
        RJ_T6[i] = RJ_U2[si];
        RJ_T7[i] = RJ_U3[si];
        RJ_T8[i] = RJ_U4[si];
    }
    rj_initialized = 1;
}

ACV_EXPORT int acv_rijndael256_init(const uint8_t key[32], uint32_t ke[15 * 8], uint32_t kd[15 * 8]) {
    if (!key || !ke || !kd) {
        return -1;
    }
    if (!rj_initialized) {
        acv_crypto_init();
    }
    uint32_t tk[8];
    for (int i = 0; i < 8; ++i) {
        tk[i] = load_be32(key + i * 4);
    }
    const int rounds = 14;
    const int round_key_count = (rounds + 1) * 8;
    int t = 0;
    for (int j = 0; j < 8 && t < round_key_count; ++j, ++t) {
        ke[t] = tk[j];
        kd[(rounds - (t >> 3)) * 8 + (t & 7)] = tk[j];
    }
    int rcon_idx = 0;
    while (t < round_key_count) {
        uint32_t tt = tk[7];
        tk[0] ^= ((uint32_t)RJ_S[(tt >> 16) & 0xffu] << 24) ^
                 ((uint32_t)RJ_S[(tt >> 8) & 0xffu] << 16) ^
                 ((uint32_t)RJ_S[tt & 0xffu] << 8) ^
                 (uint32_t)RJ_S[(tt >> 24) & 0xffu] ^
                 ((uint32_t)RJ_RCON[rcon_idx++] << 24);
        for (int i = 1; i < 4; ++i) {
            tk[i] ^= tk[i - 1];
        }
        tt = tk[3];
        tk[4] ^= (uint32_t)RJ_S[tt & 0xffu] ^
                 ((uint32_t)RJ_S[(tt >> 8) & 0xffu] << 8) ^
                 ((uint32_t)RJ_S[(tt >> 16) & 0xffu] << 16) ^
                 ((uint32_t)RJ_S[(tt >> 24) & 0xffu] << 24);
        for (int i = 5; i < 8; ++i) {
            tk[i] ^= tk[i - 1];
        }
        for (int j = 0; j < 8 && t < round_key_count; ++j, ++t) {
            ke[t] = tk[j];
            kd[(rounds - (t >> 3)) * 8 + (t & 7)] = tk[j];
        }
    }
    for (int r = 1; r < rounds; ++r) {
        uint32_t *row = kd + r * 8;
        for (int j = 0; j < 8; ++j) {
            uint32_t tt = row[j];
            row[j] = RJ_U1[(tt >> 24) & 0xffu] ^
                     RJ_U2[(tt >> 16) & 0xffu] ^
                     RJ_U3[(tt >> 8) & 0xffu] ^
                     RJ_U4[tt & 0xffu];
        }
    }
    return 0;
}

ACV_EXPORT int acv_rijndael256_cbc_encrypt(const uint32_t ke[15 * 8], const uint8_t iv[32],
                                           const uint8_t *src, uint8_t *dst, size_t len) {
    if ((len & 31u) != 0) {
        return -1;
    }
    if (len == 0) {
        return 0;
    }
    if (!ke || !iv || !src || !dst) {
        return -1;
    }
    uint32_t prev[8];
    for (int i = 0; i < 8; ++i) {
        prev[i] = load_be32(iv + i * 4);
    }
    for (size_t offset = 0; offset < len; offset += 32) {
        uint32_t t[8], a[8];
        for (int i = 0; i < 8; ++i) {
            t[i] = load_be32(src + offset + i * 4) ^ prev[i] ^ ke[i];
        }
        for (int r = 1; r < 14; ++r) {
            const uint32_t *rk = ke + r * 8;
            for (int i = 0; i < 8; ++i) {
                a[i] = (RJ_T1[(t[i] >> 24) & 0xffu] ^
                        RJ_T2[(t[(i + 1) & 7] >> 16) & 0xffu] ^
                        RJ_T3[(t[(i + 3) & 7] >> 8) & 0xffu] ^
                        RJ_T4[t[(i + 4) & 7] & 0xffu]) ^ rk[i];
            }
            for (int i = 0; i < 8; ++i) {
                t[i] = a[i];
            }
        }
        const uint32_t *rk_last = ke + 14 * 8;
        for (int i = 0; i < 8; ++i) {
            uint32_t w = (((uint32_t)RJ_S[(t[i] >> 24) & 0xffu] << 24) |
                          ((uint32_t)RJ_S[(t[(i + 1) & 7] >> 16) & 0xffu] << 16) |
                          ((uint32_t)RJ_S[(t[(i + 3) & 7] >> 8) & 0xffu] << 8) |
                          (uint32_t)RJ_S[t[(i + 4) & 7] & 0xffu]) ^ rk_last[i];
            prev[i] = w;
            store_be32(dst + offset + i * 4, w);
        }
    }
    return 0;
}

ACV_EXPORT int acv_rijndael256_cbc_decrypt(const uint32_t kd[15 * 8], const uint8_t iv[32],
                                           const uint8_t *src, uint8_t *dst, size_t len) {
    if ((len & 31u) != 0) {
        return -1;
    }
    if (len == 0) {
        return 0;
    }
    if (!kd || !iv || !src || !dst) {
        return -1;
    }
    uint32_t prev[8];
    for (int i = 0; i < 8; ++i) {
        prev[i] = load_be32(iv + i * 4);
    }
    for (size_t offset = 0; offset < len; offset += 32) {
        uint32_t ct[8], t[8], a[8];
        for (int i = 0; i < 8; ++i) {
            ct[i] = load_be32(src + offset + i * 4);
            t[i] = ct[i] ^ kd[i];
        }
        for (int r = 1; r < 14; ++r) {
            const uint32_t *rk = kd + r * 8;
            for (int i = 0; i < 8; ++i) {
                a[i] = (RJ_T5[(t[i] >> 24) & 0xffu] ^
                        RJ_T6[(t[(i + 7) & 7] >> 16) & 0xffu] ^
                        RJ_T7[(t[(i + 5) & 7] >> 8) & 0xffu] ^
                        RJ_T8[t[(i + 4) & 7] & 0xffu]) ^ rk[i];
            }
            for (int i = 0; i < 8; ++i) {
                t[i] = a[i];
            }
        }
        const uint32_t *rk_last = kd + 14 * 8;
        for (int i = 0; i < 8; ++i) {
            uint32_t w = (((uint32_t)RJ_Si[(t[i] >> 24) & 0xffu] << 24) |
                          ((uint32_t)RJ_Si[(t[(i + 7) & 7] >> 16) & 0xffu] << 16) |
                          ((uint32_t)RJ_Si[(t[(i + 5) & 7] >> 8) & 0xffu] << 8) |
                          (uint32_t)RJ_Si[t[(i + 4) & 7] & 0xffu]) ^ rk_last[i] ^ prev[i];
            prev[i] = ct[i];
            store_be32(dst + offset + i * 4, w);
        }
    }
    return 0;
}
