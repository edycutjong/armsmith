"""Detector implementations. Importing this package registers all 13 rules."""

from . import (  # noqa: F401
    r01_amd64_image,
    r02_march_flags,
    r03_reference_blas,
    r04_float64_coercion,
    r05_gguf_quant,
    r06_thread_oversub,
    r07_ort_session,
    r08_sdist_fallback,
    r09_memcpy_storm,
    r10_kleidiai_flags,
    r11_thp_allocator,
    r12_ci_matrix,
    r13_instrument_divergence,
)
