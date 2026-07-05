# DEVIATIONS from the frozen specs

Specs are frozen; this file records every place the build deliberately differs, with rationale.
(The overall hardware-free Phase-1 scope — replay probes instead of live instruments, dry-run PR,
planner stub — is the build brief's own instruction, not a deviation, and is inventoried in
PROGRESS.md under TODO(S1).)

1. **R2 descriptor `kind: probe` (ARCHITECTURE §3 says "static+runtime").**
   The Phase-1 detector consumes recorded compiler invocations (`build_log` probe) + lscpu and
   does not scan repo build files, so its declared mechanism is `probe`. A static CFLAGS/CMake
   scan half can be added at S1 without changing the descriptor contract. Rule semantics,
   fix output, and citations match the spec.

2. **R10 descriptor `kind: probe` (ARCHITECTURE §3 table says "static").**
   The KleidiAI flag state lives in the target's CMake cache — a build artifact, not repo text —
   so the detector reads a recorded `cmake_cache` probe. This follows the build brief, which
   assigns R10 to the probe/replay group; noted because the ARCHITECTURE table reads "static".

3. **Report schema extends the ARCHITECTURE §4 sketch (additive).**
   Added fields: `schema_version`, `mode` (replay|live), `synthetic`, `tool`, `scenario`,
   `gate_config`, `findings`, `plan`, per-fix `measurement` blocks with RAW samples, and the
   `signature` block. Raw-sample embedding is what makes `armsmith verify`'s recompute check
   (COMPLEXITY §2 "claimed metrics match embedded raw samples") possible. Nothing from the
   sketch was removed; `artifacts`/`cost` retained.

4. **`noise band` definition made concrete.**
   Specs say "MAD noise band" without a formula; implemented as
   `k · sqrt(smad_baseline² + smad_candidate²)` with `k = 3.0` and scaled MAD (×1.4826),
   documented in `benchstats` and echoed in every report's `gate_config`. |Δ| ≤ band (inclusive)
   is "no change".

5. **Planner budget semantics in replay mode.**
   COMPLEXITY §3's `--budget` guards hardware spend; replay diagnosis is free, so the Phase-1
   fallback planner implements the cap mechanics (`budget_usd ≤ 0` plans nothing; `max_fixes`
   truncates with a recorded note) and the `budget_remaining` tool is pinned in the S1 tool
   contract. Dollar metering of real bench runs lands with live mode.

6. **Test count 208 ≠ blueprint 150.**
   COMPLEXITY's 150 included hardware-phase categories. The hardware-free core over-delivers its
   share (208 green) while the hardware-dependent remainder is still owed — breakdown and owed
   categories in PROGRESS.md. Counts in 00-OVERVIEW/PRD (130/142) were superseded by COMPLEXITY
   pass 3 (150) per its own ripple note.

7. **`armsmith doctor` is offline-only in this phase.**
   BUILD_PLAN's `doctor` embeds sysreport on a live host. Running it here would fingerprint a
   macOS dev machine as if it were a target, so `doctor` refuses without `--offline` + a recorded
   fixture and prints the sysreport integration as TODO(S1).

8. **CI workflow included ahead of Phase 3.**
   BUILD_PLAN schedules the Action for Phase 3; a minimal `ci.yml` ships now because the replay
   suite runs on the verified free arm64 labels with zero hardware setup ("CI on ubuntu-24.04-arm
   from commit 1" per the kickoff non-negotiables). The Phase-3 Action (Performix JSON regression
   gate, cosign attest) remains TODO(S1).
