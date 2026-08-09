/* int8_mmla.c — the SECOND live workload behind Armsmith's reproduce gate.
 *
 * int8_dot.c proves one thing on real silicon: turning on +dotprod lets the
 * vectorizer emit SDOT. One kernel is a data point. This is the second, on a
 * different ISA extension and a different instruction, so the live leg reads as
 * a harness rather than one lucky microbenchmark.
 *
 * The workload is an int8 matrix multiply-accumulate — 2x8 by 8x2 into a 2x2
 * int32 block — which is the exact shape of `SMMLA`, and the shape KleidiAI and
 * llama.cpp's int8 GEMM kernels are built around. It is compiled TWICE FROM
 * THIS SAME SOURCE:
 *
 *   baseline   -O3 -march=armv8.2-a          (no i8mm: the matmul unit is off)
 *   candidate  -O3 -march=armv8.2-a+i8mm     (what the ISA fingerprint says to turn on)
 *
 * HONEST NOTE ON HOW THE SPEEDUP HAPPENS, because it differs from int8_dot.c.
 * There, the compiler's vectorizer discovers SDOT on its own from plain C. Here
 * the fast path is written with the ACLE intrinsic `vmmlaq_s32`, guarded by the
 * feature macro the flag defines. GCC does not reliably auto-vectorize this
 * shape into SMMLA, and pretending otherwise would be the sort of claim this
 * tool exists to refuse. Feature-gated kernels are also what real libraries
 * actually ship — KleidiAI selects its micro-kernel per detected CPU capability
 * in exactly this way — so the flag genuinely gates the code path, and the
 * measurement is of that gate.
 *
 * Both paths compute the SAME arithmetic, so the checksum must match. If they
 * ever diverge the gate drops the fix on output-hash inequality, which is the
 * correct outcome: a "faster" kernel that computes something else is not a fix.
 *
 * Contract with the harness (armsmith.livebench), identical to int8_dot.c:
 *   stdout -> "checksum=<int64>\n"
 *   stderr -> "kernel_s=<float>\n"
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

#if defined(__ARM_FEATURE_MATMUL_INT8)
#include <arm_neon.h>
#endif

/* Rows of A and B per block, and the shared K dimension. These are fixed by the
 * instruction: SMMLA is 2x8 * 8x2 -> 2x2, and nothing else. */
#define MR 2
#define NR 2
#define KC 8

/* noinline + external linkage for the same reason as dot_i8: the ISA witness
 * disassembles this symbol BY NAME (objdump --disassemble=mmla_i8), so it must
 * survive inlining and IPA cloning intact. */
__attribute__((noinline))
void mmla_i8(const int8_t *a, const int8_t *b, int32_t *acc)
{
#if defined(__ARM_FEATURE_MATMUL_INT8)
    /* a: 2 rows x 8 int8. b: 2 rows x 8 int8, read as the transposed 8x2.
     * acc: 2x2 int32, row-major — exactly vmmlaq_s32's operand layout. */
    int32x4_t r = vld1q_s32(acc);
    int8x16_t va = vld1q_s8(a);
    int8x16_t vb = vld1q_s8(b);
    r = vmmlaq_s32(r, va, vb);
    vst1q_s32(acc, r);
#else
    /* Bit-identical scalar equivalent: acc[i][j] += sum_k a[i][k] * b[j][k].
     * This is the definition vmmlaq_s32 implements, written out. */
    for (int i = 0; i < MR; i++) {
        for (int j = 0; j < NR; j++) {
            int32_t s = 0;
            for (int k = 0; k < KC; k++) {
                s += (int32_t)a[i * KC + k] * (int32_t)b[j * KC + k];
            }
            acc[i * NR + j] += s;
        }
    }
#endif
}

int main(int argc, char **argv)
{
    /* `n` is the number of 2x8/8x2 blocks per rep, so the two benchmarks take
     * the same argv shape and the harness needs no special-casing. */
    int n = (argc > 1) ? atoi(argv[1]) : 8192;
    int reps = (argc > 2) ? atoi(argv[2]) : 20000;
    if (n <= 0 || reps <= 0) {
        fprintf(stderr, "usage: %s [blocks] [reps]\n", argv[0]);
        return 2;
    }

    size_t bytes = (size_t)n * MR * KC;
    int8_t *a = (int8_t *)malloc(bytes);
    int8_t *b = (int8_t *)malloc(bytes);
    if (!a || !b) {
        fprintf(stderr, "allocation failed\n");
        return 3;
    }

    /* Same fixed-seed LCG as int8_dot.c: identical inputs in both builds, so a
     * checksum difference can only mean a real behavior change. */
    uint32_t seed = 20260808u;
    for (size_t i = 0; i < bytes; i++) {
        seed = seed * 1664525u + 1013904223u;
        a[i] = (int8_t)((seed >> 16) & 0xFF);
        seed = seed * 1664525u + 1013904223u;
        b[i] = (int8_t)((seed >> 16) & 0xFF);
    }

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    int64_t total = 0;
    for (int r = 0; r < reps; r++) {
        /* Defeat loop-invariant hoisting, through uint8_t so the wrap is
         * defined — signed overflow is UB, and UB is how two builds of the
         * same source stop agreeing. */
        a[(size_t)r % bytes] = (int8_t)(((uint8_t)a[(size_t)r % bytes] + 1u) & 0xFFu);

        int32_t acc[MR * NR] = {0, 0, 0, 0};
        for (int blk = 0; blk < n; blk++) {
            mmla_i8(a + (size_t)blk * MR * KC, b + (size_t)blk * MR * KC, acc);
        }
        for (int i = 0; i < MR * NR; i++) {
            total += acc[i];
        }
    }

    clock_gettime(CLOCK_MONOTONIC, &t1);
    double kernel_s = (double)(t1.tv_sec - t0.tv_sec)
                    + (double)(t1.tv_nsec - t0.tv_nsec) / 1e9;

    printf("checksum=%lld\n", (long long)total);
    fprintf(stderr, "kernel_s=%.9f\n", kernel_s);

    free(a);
    free(b);
    return 0;
}
