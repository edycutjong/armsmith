/* int8_dot.c — the workload behind Armsmith's live reproduce gate (rule R2).
 *
 * This is the quantized-inference inner loop in miniature: an int8 x int8 ->
 * int32 dot product, exactly the shape that llama.cpp/ggml, ONNX Runtime and
 * KleidiAI spend their time in.  It is compiled TWICE FROM THIS SAME SOURCE:
 *
 *   baseline   -O3 -march=armv8-a            (generic ARMv8.0 -- no dotprod)
 *   candidate  -O3 -march=armv8.2-a+dotprod  (what R2 tells you to turn on)
 *
 * Nothing else differs.  That is the whole point of R2: the source is already
 * correct, the build flags are leaving the SDOT unit switched off.  The
 * candidate build lets GCC's dot-product vectorizer emit SDOT, and
 * `armsmith.witness` counts those instructions in the disassembly -- so the
 * claim is proven at the instruction level, not just on a stopwatch.
 *
 * Contract with the harness (armsmith.livebench):
 *   stdout -> "checksum=<int64>\n"   hashed for the gate's output-equality check;
 *             both builds MUST print the same value or the fix is dropped.
 *   stderr -> "kernel_s=<float>\n"   in-process CLOCK_MONOTONIC timing of the
 *             kernel loop only, so process startup never enters the sample.
 *
 * Determinism: the input arrays come from a fixed-seed LCG, so the checksum is
 * a pure function of (n, reps) on any conforming C implementation.
 */

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <time.h>

/* The kernel is deliberately noinline AND external: noinline keeps it from
 * dissolving into main, and external linkage stops GCC's IPA passes from
 * cloning it into dot_i8.constprop.0.  Together they guarantee the ISA witness
 * can disassemble exactly this function by name
 * (objdump --disassemble=dot_i8) rather than guessing which fragment mattered. */
__attribute__((noinline))
int32_t dot_i8(const int8_t *a, const int8_t *b, int n)
{
    int32_t acc = 0;
    for (int i = 0; i < n; i++) {
        /* Widening to int32 before the multiply is what makes this a
         * DOT_PROD_EXPR to GCC's vectorizer; with +dotprod it becomes SDOT. */
        acc += (int32_t)a[i] * (int32_t)b[i];
    }
    return acc;
}

int main(int argc, char **argv)
{
    int n = (argc > 1) ? atoi(argv[1]) : 8192;
    int reps = (argc > 2) ? atoi(argv[2]) : 20000;
    if (n <= 0 || reps <= 0) {
        fprintf(stderr, "usage: %s [n] [reps]\n", argv[0]);
        return 2;
    }

    int8_t *a = (int8_t *)malloc((size_t)n);
    int8_t *b = (int8_t *)malloc((size_t)n);
    if (!a || !b) {
        fprintf(stderr, "allocation failed\n");
        return 3;
    }

    /* Fixed-seed LCG (Numerical Recipes constants): identical inputs in both
     * builds, so any checksum difference is a real behavior change. */
    uint32_t seed = 20260808u;
    for (int i = 0; i < n; i++) {
        seed = seed * 1664525u + 1013904223u;
        a[i] = (int8_t)((seed >> 16) & 0xFF);
        seed = seed * 1664525u + 1013904223u;
        b[i] = (int8_t)((seed >> 16) & 0xFF);
    }

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);

    int64_t total = 0;
    for (int r = 0; r < reps; r++) {
        /* Mutating one input element per rep defeats loop-invariant hoisting:
         * without this the compiler computes the dot product once and the
         * "benchmark" measures nothing.  The increment goes through uint8_t so
         * the wrap is defined (signed char overflow would be UB, and UB is
         * exactly how two builds of "the same" source stop agreeing). */
        a[r % n] = (int8_t)(((uint8_t)a[r % n] + 1u) & 0xFFu);
        total += dot_i8(a, b, n);
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
