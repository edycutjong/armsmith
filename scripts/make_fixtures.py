#!/usr/bin/env python3
"""Generate the synthetic fixture corpus under fixtures/.

EVERY value written here is HAND-AUTHORED SYNTHETIC DATA for offline tests:
realistic in *shape* (llama-bench JSON, hyperfine JSON, lscpu text, GGUF
headers, perf report text, numpy show_config text) but never measured on any
hardware. Each bundle's manifest.json carries `"synthetic": true` and a
provenance note; loaders refuse unlabeled bundles.

Idempotent: re-running rewrites the same content. Run from the repo root:

    .venv/bin/python scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "fixtures"
sys.path.insert(0, str(ROOT / "src"))

from armsmith.gguf import build_stub  # noqa: E402

PROVENANCE = (
    "hand-authored synthetic fixture for offline tests; values are "
    "illustrative shapes only and were NOT measured on any hardware"
)


def manifest(scenario: str, host: dict | None = None, extra: dict | None = None) -> dict:
    data = {"synthetic": True, "provenance": PROVENANCE, "scenario": scenario}
    if host:
        data["host"] = host
    if extra:
        data.update(extra)
    return data


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def write_bytes(path: Path, blob: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)


# ---------------------------------------------------------------------------
# lscpu shapes (synthetic; flag sets modeled on public Neoverse documentation)
# ---------------------------------------------------------------------------

LSCPU_V2 = """\
Architecture:                         aarch64
CPU op-mode(s):                       64-bit
Byte Order:                           Little Endian
CPU(s):                               16
On-line CPU(s) list:                  0-15
Vendor ID:                            ARM
Model name:                           Neoverse-V2
Model:                                1
Thread(s) per core:                   1
Core(s) per socket:                   16
Socket(s):                            1
Stepping:                             r0p1
BogoMIPS:                             2100.00
Flags:                                fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm jscvt fcma lrcpc dcpop sha3 sm3 sm4 asimddp sha512 sve asimdfhm dit uscat ilrcpc flagm ssbs sb paca pacg dcpodp sve2 sveaes svepmull svebitperm svesha3 svesm4 flagm2 frint svei8mm svebf16 i8mm bf16 dgh rng bti
"""

LSCPU_N1 = """\
Architecture:                         aarch64
CPU op-mode(s):                       32-bit, 64-bit
Byte Order:                           Little Endian
CPU(s):                               2
On-line CPU(s) list:                  0-1
Vendor ID:                            ARM
Model name:                           Neoverse-N1
Model:                                1
Thread(s) per core:                   1
Core(s) per socket:                   2
Socket(s):                            1
Stepping:                             r3p1
BogoMIPS:                             243.75
Flags:                                fp asimd evtstrm aes pmull sha1 sha2 crc32 atomics fphp asimdhp cpuid asimdrdm lrcpc dcpop asimddp ssbs
"""

LSCPU_A53 = """\
Architecture:                         aarch64
CPU op-mode(s):                       32-bit, 64-bit
Byte Order:                           Little Endian
CPU(s):                               4
On-line CPU(s) list:                  0-3
Vendor ID:                            ARM
Model name:                           Cortex-A53
Flags:                                fp asimd evtstrm aes pmull sha1 sha2 crc32 cpuid
"""


def hosts() -> None:
    d = FIXTURES / "hosts"
    write(d / "lscpu_neoverse_v2.txt", LSCPU_V2)
    write(d / "lscpu_neoverse_n1.txt", LSCPU_N1)
    write(d / "lscpu_cortex_a53.txt", LSCPU_A53)
    write_json(d / "PROVENANCE.json", manifest("host fingerprint text shapes"))


# ---------------------------------------------------------------------------
# per-rule positive/negative bundles
# ---------------------------------------------------------------------------

def rule_bundle(name: str) -> Path:
    d = FIXTURES / "rules" / name
    write_json(d / "manifest.json", manifest(f"rule fixture {name}"))
    return d


def rules() -> None:
    # R1 — amd64-pinned image (static)
    d = rule_bundle("r01_pos")
    write(d / "repo" / "Dockerfile",
          "FROM --platform=linux/amd64 python:3.12-slim\n"
          "COPY . /app\nRUN pip install -r /app/requirements.txt\nCMD [\"python\", \"/app/serve.py\"]\n")
    write(d / "repo" / "docker-compose.yml",
          "services:\n  api:\n    build: .\n    platform: linux/amd64\n")
    d = rule_bundle("r01_neg")
    write(d / "repo" / "Dockerfile",
          "FROM python:3.12-slim\nCOPY . /app\nRUN pip install -r /app/requirements.txt\nCMD [\"python\", \"/app/serve.py\"]\n")

    # R2 — missing -mcpu/-march (probe: build_log + lscpu)
    d = rule_bundle("r02_pos")
    write(d / "probes" / "build_log.txt",
          "gcc -O3 -fPIC -c src/fastpath.c -o build/fastpath.o\n"
          "gcc -O3 -fPIC -c src/tokenize.c -o build/tokenize.o\n"
          "gcc -shared build/fastpath.o build/tokenize.o -o build/_native.so\n")
    write(d / "probes" / "lscpu.txt", LSCPU_V2)
    d = rule_bundle("r02_neg")
    write(d / "probes" / "build_log.txt",
          "gcc -O3 -mcpu=neoverse-v2 -fPIC -c src/fastpath.c -o build/fastpath.o\n"
          "gcc -O3 -mcpu=neoverse-v2 -fPIC -c src/tokenize.c -o build/tokenize.o\n")
    write(d / "probes" / "lscpu.txt", LSCPU_V2)

    # R3 — reference BLAS (probe: numpy_show_config)
    d = rule_bundle("r03_pos")
    write(d / "probes" / "numpy_show_config.txt",
          "blas_info:\n    libraries = ['blas', 'cblas']\n    library_dirs = ['/usr/lib/aarch64-linux-gnu']\n"
          "    language = c\nblas_opt_info:\n    libraries = ['blas', 'cblas']\n"
          "lapack_info:\n    libraries = ['lapack']\n"
          "openblas_info:\n  NOT AVAILABLE\nopenblas_lapack_info:\n  NOT AVAILABLE\n")
    d = rule_bundle("r03_neg")
    write(d / "probes" / "numpy_show_config.txt",
          "Build Dependencies:\n  blas:\n    detection method: pkgconfig\n    found: true\n"
          "    include directory: /usr/local/include\n    name: openblas64\n"
          "    openblas configuration: USE_64BITINT=1 DYNAMIC_ARCH=0 NEOVERSEV1 MAX_THREADS=64\n"
          "    version: 0.3.27\n  lapack:\n    name: openblas64\n    found: true\n")

    # R4 — float64 coercion (static)
    d = rule_bundle("r04_pos")
    write(d / "repo" / "embed.py",
          "import numpy as np\n\n\ndef embed(batch):\n"
          "    vecs = np.array(batch)  # silent float64\n"
          "    buf = np.zeros((len(batch), 768))\n"
          "    return vecs @ buf.T\n")
    d = rule_bundle("r04_neg")
    write(d / "repo" / "embed.py",
          "import numpy as np\n\n\ndef embed(batch):\n"
          "    vecs = np.array(batch, dtype=np.float32)\n"
          "    buf = np.zeros((len(batch), 768), dtype=np.float32)\n"
          "    return vecs @ buf.T\n")

    # R5 — GGUF quant vs ISA (probe: gguf_header + lscpu)
    d = rule_bundle("r05_pos")  # K-quant on a dotprod/i8mm host → repack path unused
    write_bytes(d / "probes" / "gguf_header.bin", build_stub(file_type=15))  # Q4_K_M
    write(d / "probes" / "lscpu.txt", LSCPU_V2)
    d = rule_bundle("r05_neg")  # Q4_0 on a dotprod host → repack engages, consistent
    write_bytes(d / "probes" / "gguf_header.bin", build_stub(file_type=2))   # Q4_0
    write(d / "probes" / "lscpu.txt", LSCPU_N1)
    d = rule_bundle("r05_pos_nodotprod")  # Q4_0 on a host WITHOUT dotprod → repack can't engage
    write_bytes(d / "probes" / "gguf_header.bin", build_stub(file_type=2))
    write(d / "probes" / "lscpu.txt", LSCPU_A53)

    # R6 — thread oversubscription (probe: env)
    d = rule_bundle("r06_pos")
    write_json(d / "probes" / "env.json",
               {"env": {}, "workers": 4, "nproc": 16,
                "worker_source": "gunicorn -w 4 app:app (recorded)"})
    d = rule_bundle("r06_neg")
    write_json(d / "probes" / "env.json",
               {"env": {"OMP_NUM_THREADS": "4", "OPENBLAS_NUM_THREADS": "4"},
                "workers": 4, "nproc": 16, "worker_source": "gunicorn -w 4 app:app (recorded)"})

    # R7 — ORT session defaults (probe: ort_session)
    d = rule_bundle("r07_pos")
    write_json(d / "probes" / "ort_session.json",
               {"intra_op_num_threads": 0, "inter_op_num_threads": 0,
                "graph_optimization_level": "ORT_ENABLE_BASIC",
                "execution_mode": "ORT_SEQUENTIAL", "workers": 4, "nproc": 16})
    d = rule_bundle("r07_neg")
    write_json(d / "probes" / "ort_session.json",
               {"intra_op_num_threads": 4, "inter_op_num_threads": 1,
                "graph_optimization_level": "ORT_ENABLE_ALL",
                "execution_mode": "ORT_SEQUENTIAL", "workers": 4, "nproc": 16})

    # R8 — pip sdist fallback (probe: pip_install_log)
    d = rule_bundle("r08_pos")
    write(d / "probes" / "pip_install_log.txt",
          "Collecting numpy==1.24.0\n"
          "  Downloading numpy-1.24.0.tar.gz (10.9 MB)\n"
          "  Installing build dependencies: started\n"
          "Building wheels for collected packages: numpy\n"
          "  Building wheel for numpy (pyproject.toml): started\n"
          "  Building wheel for numpy (pyproject.toml): finished with status 'done'\n"
          "Collecting requests\n"
          "  Downloading requests-2.32.3-py3-none-any.whl (64 kB)\n")
    d = rule_bundle("r08_neg")
    write(d / "probes" / "pip_install_log.txt",
          "Collecting numpy==1.26.4\n"
          "  Downloading numpy-1.26.4-cp312-cp312-manylinux_2_17_aarch64.manylinux2014_aarch64.whl (14.2 MB)\n"
          "Collecting requests\n"
          "  Downloading requests-2.32.3-py3-none-any.whl (64 kB)\n"
          "Installing collected packages: numpy, requests\n")

    # R9 — memcpy storm (probe: perf_report)
    d = rule_bundle("r09_pos")
    write(d / "probes" / "perf_report.txt",
          "# Overhead  Command  Shared Object       Symbol\n"
          "# ........  .......  ..................  ..........................\n"
          "    28.40%  python   libc.so.6           [.] __memcpy_generic\n"
          "     9.10%  python   libc.so.6           [.] __memmove_generic\n"
          "     8.75%  python   _tokenizer.so       [.] tokenize_batch\n"
          "     4.20%  python   libpython3.12.so    [.] PyBytes_Concat\n"
          "     2.05%  python   libc.so.6           [.] __memset_generic\n")
    d = rule_bundle("r09_neg")
    write(d / "probes" / "perf_report.txt",
          "# Overhead  Command  Shared Object       Symbol\n"
          "    41.30%  python   libggml.so          [.] ggml_vec_dot_q4_0_q8_0\n"
          "    12.10%  python   libggml.so          [.] ggml_compute_forward_mul_mat\n"
          "     5.90%  python   libc.so.6           [.] __memcpy_generic\n"
          "     3.10%  python   libopenblas.so      [.] sgemm_kernel\n")

    # R10 — KleidiAI flags (probe: cmake_cache)
    d = rule_bundle("r10_pos")
    write(d / "probes" / "cmake_cache.txt",
          "CMAKE_BUILD_TYPE:STRING=Release\n"
          "GGML_NATIVE:BOOL=OFF\n"
          "GGML_CPU_KLEIDIAI:BOOL=OFF\n"
          "GGML_BLAS:BOOL=OFF\n"
          "LLAMA_BUILD_SERVER:BOOL=ON\n")
    d = rule_bundle("r10_neg")
    write(d / "probes" / "cmake_cache.txt",
          "CMAKE_BUILD_TYPE:STRING=Release\n"
          "GGML_NATIVE:BOOL=ON\n"
          "GGML_CPU_KLEIDIAI:BOOL=ON\n"
          "GGML_BLAS:BOOL=OFF\n"
          "LLAMA_BUILD_SERVER:BOOL=ON\n")
    d = rule_bundle("r10_na")  # not a ggml build → clean
    write(d / "probes" / "cmake_cache.txt",
          "CMAKE_BUILD_TYPE:STRING=Release\nBUILD_SHARED_LIBS:BOOL=ON\n")

    # R11 — THP/allocator (probes: thp + proc_maps)
    d = rule_bundle("r11_pos")
    write(d / "probes" / "thp.txt", "always madvise [never]\n")
    write(d / "probes" / "proc_maps.txt",
          "7f2a00000000-7f2a3c000000 rw-p 00000000 00:00 0        [heap]\n"
          "7f2a40000000-7f2a40040000 r-xp 00000000 08:01 393  /usr/lib/aarch64-linux-gnu/libc.so.6\n")
    d = rule_bundle("r11_neg")
    write(d / "probes" / "thp.txt", "always [madvise] never\n")
    write(d / "probes" / "proc_maps.txt",
          "7f2a00000000-7f2a3c000000 rw-p 00000000 00:00 0        [heap]\n"
          "7f2a40000000-7f2a40040000 r-xp 00000000 08:01 393  /usr/lib/aarch64-linux-gnu/libc.so.6\n"
          "7f2a41000000-7f2a41080000 r-xp 00000000 08:01 401  /usr/lib/aarch64-linux-gnu/libjemalloc.so.2\n")

    # R12 — CI matrix (static)
    d = rule_bundle("r12_pos")
    write(d / "repo" / ".github" / "workflows" / "publish.yml",
          "name: publish\non:\n  push:\n    tags: ['v*']\njobs:\n  image:\n"
          "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
          "      - uses: docker/setup-buildx-action@v3\n"
          "      - uses: docker/build-push-action@v6\n        with:\n"
          "          push: true\n          tags: ghcr.io/example/app:latest\n")
    d = rule_bundle("r12_neg")
    write(d / "repo" / ".github" / "workflows" / "publish.yml",
          "name: publish\non:\n  push:\n    tags: ['v*']\njobs:\n  image:\n"
          "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
          "      - uses: docker/setup-qemu-action@v3\n"
          "      - uses: docker/setup-buildx-action@v3\n"
          "      - uses: docker/build-push-action@v6\n        with:\n"
          "          push: true\n          platforms: linux/amd64,linux/arm64\n"
          "          tags: ghcr.io/example/app:latest\n")

    # R13 — two-instrument divergence (probes: llama_bench + hyperfine)
    # Self-reported avg/stddev are COMPUTED from the samples (ddof=1) so the
    # instrument-consistency crosscheck holds by construction — except in the
    # deliberately-corrupt bundle below.
    import statistics

    def lb_entry(n_prompt: int, n_gen: int, samples: list[float]) -> dict:
        return {
            "build_commit": "synthetic", "model_type": "synthetic 3B",
            "n_prompt": n_prompt, "n_gen": n_gen,
            "avg_ts": round(statistics.fmean(samples), 6),
            "stddev_ts": round(statistics.stdev(samples), 6),
            "samples_ts": samples,
        }

    def hf_result(command: str, times: list[float]) -> dict:
        return {
            "results": [{
                "command": command,
                "mean": round(statistics.fmean(times), 6),
                "stddev": round(statistics.stdev(times), 6),
                "median": round(statistics.median(times), 6),
                "min": min(times), "max": max(times),
                "times": times,
                "exit_codes": [0] * len(times),
            }],
        }

    PP_SAMPLES = [400.0, 396.2, 404.1, 399.5, 402.3, 397.8, 400.8]
    TG_SAMPLES = [50.0, 49.6, 50.5, 49.9, 50.3, 49.7, 50.1]

    # pos: kernel time ≈ 512/400.0 + 128/50.0 = 1.28 + 2.56 = 3.84s; E2E median 5.6s
    #      → overhead 1.76s = 31.4% > 15% → fires.
    d = rule_bundle("r13_pos")
    write_json(d / "probes" / "llama_bench.json",
               [lb_entry(512, 0, PP_SAMPLES), lb_entry(0, 128, TG_SAMPLES)])
    write_json(d / "probes" / "hyperfine.json", hf_result(
        "python summarize.py --prompt-file prompts_50.txt",
        [5.60, 5.55, 5.68, 5.58, 5.63, 5.52, 5.71]))

    # neg: kernel 3.84s vs E2E 4.02s → 4.5% overhead → clean.
    d = rule_bundle("r13_neg")
    write_json(d / "probes" / "llama_bench.json",
               [lb_entry(512, 0, PP_SAMPLES), lb_entry(0, 128, TG_SAMPLES)])
    write_json(d / "probes" / "hyperfine.json", hf_result(
        "llama-cli -m model.gguf -p @prompt.txt -n 128 --no-display-prompt",
        [4.02, 3.99, 4.08, 4.00, 4.05, 3.97, 4.10]))

    # corrupt: llama-bench self-report disagrees with samples → rule SKIPS
    d = rule_bundle("r13_corrupt")
    corrupt = lb_entry(0, 128, TG_SAMPLES)
    corrupt["avg_ts"] = 90.0  # claims 90 t/s; its own samples say ~50
    write_json(d / "probes" / "llama_bench.json", [corrupt])
    write_json(d / "probes" / "hyperfine.json", hf_result(
        "python summarize.py", [5.60, 5.55, 5.68, 5.58, 5.63, 5.52, 5.71]))


# ---------------------------------------------------------------------------
# ISA-witness disassembly shapes (objdump -d text)
# ---------------------------------------------------------------------------

OBJDUMP_BEFORE = """\
0000000000401000 <gemm_kernel_generic>:
  401000:\ta9be7bfd \tstp\tx29, x30, [sp, #-32]!
  401004:\t910003fd \tmov\tx29, sp
  401008:\t4ea01c00 \tmov\tv0.16b, v0.16b
  40100c:\t4e20d401 \tfadd\tv1.4s, v0.4s, v0.4s
  401010:\t4e21d422 \tfadd\tv2.4s, v1.4s, v1.4s
  401014:\t6e21dc23 \tfmul\tv3.4s, v1.4s, v1.4s
  401018:\tf9400021 \tldr\tx1, [x1]
  40101c:\t91000421 \tadd\tx1, x1, #0x1
  401020:\ta8c27bfd \tldp\tx29, x30, [sp], #32
  401024:\td65f03c0 \tret
"""

OBJDUMP_AFTER = """\
0000000000401000 <gemm_kernel_dotprod>:
  401000:\ta9be7bfd \tstp\tx29, x30, [sp, #-32]!
  401004:\t910003fd \tmov\tx29, sp
  401008:\t4e809400 \tsdot\tv0.4s, v0.16b, v0.16b
  40100c:\t4e819421 \tsdot\tv1.4s, v1.16b, v1.16b
  401010:\t6e829442 \tudot\tv2.4s, v2.16b, v2.16b
  401014:\t4e83a463 \tsmmla\tv3.4s, v3.16b, v3.16b
  401018:\t6e84a484 \tusmmla\tv4.4s, v4.16b, v4.16b
  40101c:\t4e8594a5 \tsdot\tv5.4s, v5.16b, v5.16b
  401020:\ta8c27bfd \tldp\tx29, x30, [sp], #32
  401024:\td65f03c0 \tret
"""


def witness() -> None:
    d = FIXTURES / "witness"
    write(d / "objdump_before.txt", OBJDUMP_BEFORE)
    write(d / "objdump_after.txt", OBJDUMP_AFTER)
    write_json(d / "PROVENANCE.json", manifest("ISA-witness objdump text shapes"))


# ---------------------------------------------------------------------------
# full demo scenario bundle (drives the CLI e2e test)
# ---------------------------------------------------------------------------

def bench_record(variant, rule_id, metrics, pmu, output_sha, instrument="hyperfine"):
    return {
        "synthetic": True,
        "provenance": PROVENANCE,
        "variant": variant,
        "rule_id": rule_id,
        "instrument": instrument,
        "metrics": metrics,
        "pmu": pmu,
        "output_sha256": output_sha,
    }


OUT_HASH = "a3f5" * 16  # stable synthetic output hash
OUT_HASH_CHANGED = "b4e6" * 16


def scenario() -> None:
    d = FIXTURES / "replays" / "scenario_ragserve"
    write_json(d / "manifest.json", manifest(
        "scenario_ragserve",
        host={"instance": "synthetic-c8g.4xlarge", "kernel": "6.8.0-synthetic",
              "governor": "performance"},
        extra={"repo": {"url": "https://github.com/example/ragserve", "sha": "0" * 40},
               "note": "planted-flaw demo repo per SEED_DATA.md; all numbers synthetic"},
    ))

    # -- mini repo (static-rule targets) -----------------------------------
    write(d / "repo" / "Dockerfile",
          "FROM --platform=linux/amd64 python:3.12-slim\n"
          "WORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt\n"
          "CMD [\"gunicorn\", \"-w\", \"4\", \"app:app\"]\n")
    write(d / "repo" / "app.py",
          "import numpy as np\n\n\ndef embed(batch):\n"
          "    vecs = np.array(batch)  # planted: silent float64 (R4)\n"
          "    scale = np.ones(len(batch))\n"
          "    return vecs * scale[:, None]\n")
    write(d / "repo" / ".github" / "workflows" / "publish.yml",
          "name: publish\non:\n  push:\n    tags: ['v*']\njobs:\n  image:\n"
          "    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
          "      - uses: docker/build-push-action@v6\n        with:\n"
          "          push: true\n          tags: ghcr.io/example/ragserve:latest\n")

    # -- probes -------------------------------------------------------------
    p = d / "probes"
    write(p / "lscpu.txt", LSCPU_V2)
    write(p / "numpy_show_config.txt",
          "blas_info:\n    libraries = ['blas', 'cblas']\nopenblas_info:\n  NOT AVAILABLE\n")
    write_json(p / "env.json",
               {"env": {}, "workers": 4, "nproc": 16,
                "worker_source": "Dockerfile CMD gunicorn -w 4 (recorded)"})
    write(p / "pip_install_log.txt",
          "Collecting numpy==1.24.0\n  Downloading numpy-1.24.0.tar.gz (10.9 MB)\n"
          "Building wheels for collected packages: numpy\n"
          "  Building wheel for numpy (pyproject.toml): finished with status 'done'\n")
    write(p / "perf_report.txt",
          "# Overhead  Command  Shared Object     Symbol\n"
          "    38.20%  python   libblas.so.3      [.] dgemm_\n"
          "    11.40%  python   libpython3.12.so  [.] _PyEval_EvalFrameDefault\n"
          "     6.10%  python   libc.so.6         [.] __memcpy_generic\n")
    write(p / "thp.txt", "always madvise [never]\n")
    write(p / "proc_maps.txt",
          "7f2a00000000-7f2a3c000000 rw-p 00000000 00:00 0        [heap]\n"
          "7f2a40000000-7f2a40040000 r-xp 00000000 08:01 393  /usr/lib/aarch64-linux-gnu/libc.so.6\n")

    # -- bench records (gate inputs) ----------------------------------------
    b = d / "bench"
    base_pmu = {"cycles": 8.0e9, "instructions": 1.0e10, "ipc": 1.25, "cache_miss_pct": 8.2}
    write_json(b / "baseline.json", bench_record(
        "baseline", None,
        {"wall_s": [2.01, 1.98, 2.03, 2.00, 1.99, 2.02, 2.00],
         "rss_peak_mb": [512.0, 514.0, 511.0, 513.0, 512.0, 512.5, 513.5]},
        base_pmu, OUT_HASH))

    write_json(b / "fix_R1.json", bench_record(
        "fix_R1", "R1",
        {"wall_s": [0.71, 0.69, 0.72, 0.70, 0.70, 0.71, 0.69],
         "rss_peak_mb": [498.0, 500.0, 497.0, 499.0, 498.5, 499.5, 498.0]},
        {"cycles": 2.9e9, "instructions": 6.4e9, "ipc": 2.21, "cache_miss_pct": 4.1},
        OUT_HASH))

    write_json(b / "fix_R3.json", bench_record(
        "fix_R3", "R3",
        {"wall_s": [1.31, 1.29, 1.32, 1.30, 1.30, 1.31, 1.29],
         "rss_peak_mb": [512.0, 513.0, 511.5, 512.5, 512.0, 513.0, 512.0]},
        {"cycles": 5.3e9, "instructions": 8.9e9, "ipc": 1.68, "cache_miss_pct": 6.0},
        OUT_HASH))

    write_json(b / "fix_R4.json", bench_record(
        "fix_R4", "R4",
        {"wall_s": [1.72, 1.70, 1.74, 1.71, 1.72, 1.73, 1.70],
         "rss_peak_mb": [312.0, 314.0, 311.0, 313.0, 312.0, 312.5, 313.0]},
        {"cycles": 6.9e9, "instructions": 9.4e9, "ipc": 1.36, "cache_miss_pct": 5.4},
        OUT_HASH))

    write_json(b / "fix_R6.json", bench_record(
        "fix_R6", "R6",
        {"wall_s": [1.56, 1.54, 1.57, 1.55, 1.55, 1.56, 1.54],
         "rss_peak_mb": [508.0, 510.0, 507.0, 509.0, 508.5, 509.0, 508.0]},
        {"cycles": 6.2e9, "instructions": 9.9e9, "ipc": 1.60, "cache_miss_pct": 7.1},
        OUT_HASH))

    # honest drop #1: R11 fix lands inside the noise band → "no change"
    write_json(b / "fix_R11.json", bench_record(
        "fix_R11", "R11",
        {"wall_s": [2.00, 1.97, 2.02, 2.00, 1.98, 2.01, 2.00],
         "rss_peak_mb": [511.0, 513.0, 510.5, 512.0, 511.5, 512.0, 512.5]},
        {"cycles": 7.9e9, "instructions": 1.0e10, "ipc": 1.27, "cache_miss_pct": 8.0},
        OUT_HASH))

    # honest drop #2: R8 wheel pin changes outputs → hash mismatch → drop
    write_json(b / "fix_R8.json", bench_record(
        "fix_R8", "R8",
        {"wall_s": [1.62, 1.60, 1.63, 1.61, 1.61, 1.62, 1.60],
         "rss_peak_mb": [505.0, 507.0, 504.0, 506.0, 505.5, 506.0, 505.0]},
        {"cycles": 6.5e9, "instructions": 9.6e9, "ipc": 1.48, "cache_miss_pct": 6.6},
        OUT_HASH_CHANGED))


def main() -> None:
    hosts()
    rules()
    witness()
    scenario()
    count = sum(1 for p in FIXTURES.rglob("*") if p.is_file())
    print(f"fixtures written under {FIXTURES} ({count} files) — ALL SYNTHETIC")


if __name__ == "__main__":
    main()
