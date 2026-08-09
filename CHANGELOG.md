# CHANGELOG


## v1.3.0 (2026-08-09)

### Continuous Integration

- Require the SMMLA witness now that the runner has proven it
  ([`216fecb`](https://github.com/edycutjong/armsmith/commit/216fecb8222278eb70f5977192faabe32721e4a3))

The mmla case landed without --require-witness so an unexpected toolchain result would report rather
  than redden the build. The runner has now measured SMMLA 0 -> 1 with verdict keep, so the
  instruction demonstrably emits there.

Turning the assertion on: if a future GCC or runner image stops emitting SMMLA, the premise of this
  case is gone and CI should fail loudly rather than keep timing two builds that are secretly
  identical.

### Features

- **bench-cmd**: Point the reproduce gate at the operator's own workload
  ([`92b6edd`](https://github.com/edycutjong/armsmith/commit/92b6eddd0798c283b5ff19bed0bc5dcecda54710))

The gate is the whole product, and it could only ever be aimed at this project's own
  bench/int8_dot.c. So the one thing the tool is FOR - deciding whether a proposed fix is real -
  could not be applied to a fix you actually made. An independent review put it plainly: the value
  proposition in the tagline was not deliverable by a stranger.

armsmith bench-cmd --rule R3 \ --baseline-cmd "python serve_bench.py --config before.yaml" \
  --candidate-cmd "python serve_bench.py --config after.yaml"

Identical statistics to everything else here: ABAB interleaving so drift lands on both sides,
  median-of-N, scaled-MAD noise band, output-hash equality, and an ed25519-signed report that
  `armsmith verify` re-derives from the embedded raw samples. A delta inside the band is reported as
  no change, never as a win.

What it deliberately does NOT do:

- No ISA witness. There is no binary to disassemble, so artifacts record isa_witness.available =
  false with a pointer to bench-live, rather than zero counters a reader could mistake for a
  measurement. A test asserts no numeric counter ever appears there. - No running off aarch64. A
  wall-clock number from an x86 box is not an Arm result; require_arm=False exists for a deliberate
  non-Arm comparison and the host arch is recorded either way. - No non-deterministic workloads. If
  stdout changes between runs the gate cannot tell an improvement from a different computation, so
  it refuses to measure rather than compare two different things.

Fixes a bug found by testing verify on its own output: a run without --rule stamped rule_id "-" into
  the finding, which fails the report schema's ^R\d+$ and made a signed report unverifiable. An
  operator-driven A/B now emits no finding at all rather than inventing a rule id - a fake id in a
  signed report is exactly what this tool exists not to do. The commands are recorded in
  artifacts.workload regardless.

Also adds the R9 before/after snippet, so 12 of 13 migration cards now carry a paste-able diff; only
  R13 stays diagnostic.

Verified end to end on a real numpy float64-vs-float32 workload: +4.42% inside a +/-0.164 s band ->
  no_change -> gate drop. It refused to claim a win from noise on a workload it had never seen. 447
  tests, 100% coverage.

- **bench-live**: Second live case — SMMLA under +i8mm
  ([`47a953e`](https://github.com/edycutjong/armsmith/commit/47a953ebaf58c9a1862f2e3e59945f3ae5868b93))

One measured kernel is a data point. The live leg now runs two, on different ISA extensions and
  different instructions, so it reads as a harness rather than one lucky microbenchmark:

--case dot (default) int8_dot.c +dotprod -> SDOT --case mmla int8_mmla.c +i8mm -> SMMLA

bench/int8_mmla.c is a 2x8 * 8x2 -> 2x2 int8 matmul, the exact shape SMMLA implements and the shape
  KleidiAI and llama.cpp's int8 GEMM kernels are built around. Compiled twice from one source,
  differing only in the -march flag.

HONEST about how the speedup happens, because it differs from the dot case. There, GCC's vectorizer
  discovers SDOT from plain C. Here the fast path is the ACLE intrinsic vmmlaq_s32 behind
  __ARM_FEATURE_MATMUL_INT8, because GCC does not reliably auto-vectorize this shape and pretending
  otherwise would be the kind of claim this tool exists to refuse. The source says so in its header.
  Feature-gated kernels are also what real libraries ship - KleidiAI selects its micro-kernel per
  detected CPU capability exactly this way - so the flag genuinely gates the code path, and that
  gate is what is measured. Both paths compute identical arithmetic, so any divergence is dropped by
  the gate on output-hash inequality.

Verified locally before landing: the +i8mm build contains 1 SMMLA in mmla_i8 and the baseline
  contains 0 - the same 0->1 witness shape as SDOT. (The intrinsic build SIGILLs on this M1 Max,
  which has no i8mm; that is the instruction being real, not a defect.)

livebench is now parameterised by a BenchCase - source, symbol, both variant specs, rule id,
  scenario - instead of hardcoding int8_dot.c and dot_i8, and the report's workload block records
  which case ran.

CI runs the new case on the arm64 runner and prints the SMMLA counts. It deliberately does NOT pass
  --require-witness on this first landing: the counts are asserted by reading the signed report, so
  an unexpected toolchain result is reported rather than turning the build red.

Also de-flakes the bench-cmd --strict test, which depended on two identical commands landing inside
  the noise band - true only usually, and a test that turns on scheduler luck fails in CI for no
  reason. It now uses a candidate that is deliberately slower.

449 tests, 100% coverage.


## v1.2.2 (2026-08-09)

### Bug Fixes

- **rules**: Stop R4 suggesting a patch it cannot prove; resolve R12 matrix vars
  ([`d59b8d7`](https://github.com/edycutjong/armsmith/commit/d59b8d7361b41488374253390aa7b091609bbed2))

Two false-positive classes, both found by pointing the tool at real repos.

R4 — the surviving finding still carried the corrupting patch. The dtype guard cut TGI from 5
  findings to 1, but that one printed "add dtype=np.float32" for np.array(adapter_indices) — exactly
  the edit the rule's own docstring warns would break an index array. Reporting the call is right;
  suggesting that fix for it is not. Calls are now classified PROVEN (the constructor or a literal
  payload guarantees float64) vs UNPROVABLE (a name whose element type we cannot see). PROVEN keeps
  the mechanical patch. UNPROVABLE is reported as a question - "CONFIRM the payload is float before
  pinning dtype ... leave this call alone" - and Fix.kind becomes "advisory" when nothing is
  provable.

Also recognises integer comprehensions: np.array of a range comprehension is int64, and was being
  flagged on vllm.

R12 — an unresolved matrix expression was reported as a match. The platforms input was read
  literally, so llama.cpp was told it had no arm64 while its matrix explicitly includes linux/arm64.
  The rule now resolves matrix paths against the job's own strategy block (including include:
  entries). When the matrix is built at runtime and cannot be resolved, the rule stays SILENT and
  says so rather than guessing - silence beats a false positive. Status gates on locations, not
  evidence, so those notes cannot make the rule fire.

Verified on the repos that exposed them: llama.cpp CLEAN, TGI still MATCHED on its genuinely
  amd64-only build, vllm's comprehension no longer flagged. 429 tests, 100% coverage.

Also in this commit — provenance labelling, which had drifted:

- cli.py printed the REPLAY MODE / synthetic banner unconditionally. It now reads
  report["synthetic"], so a recorded bundle gets "RECORDED - real observations captured on a host"
  instead of being called fabricated. - The PR body stamped "SYNTHETIC DATA" on any replayed report,
  including bundles whose manifest says synthetic:false. - README claimed "every other report
  carries mode replay + synthetic true", contradicting the provenance table twelve sections earlier.
  - The live-measurement table is pinned to the run it came from (31301665280) with that run's real
  figures, and now says out loud why the last digits move between runs: the job re-measures on every
  push, and a number that never drifted would be a number nobody was measuring.

CI: the arm64 job now runs record -> diagnose. Everywhere else in CI the probe rules read fixtures
  we wrote; on that runner lscpu and the THP sysfs node genuinely exist, so it is the only place a
  bundle can come from observations nobody authored. The step asserts synthetic:false and that lscpu
  was really captured, so a green tick cannot mean "captured nothing".

UX: `armsmith --version` works (it is what people type first); `doctor` fails ONCE with the complete
  working invocation instead of teaching itself through two consecutive errors; action.yml and the
  README Action snippet now say that `replay:` points at a bundle in YOUR repo and show how to
  record one.

Kitchen leak: rule packs, detectors and generated migration cards cited crawl/clean/sdk_*.md -
  private research paths absent from this repo. Replaced with the public upstream docs they
  summarise.


## v1.2.1 (2026-08-09)

### Bug Fixes

- **packaging**: Give PyPI its own README so the page actually renders
  ([`3c7af38`](https://github.com/edycutjong/armsmith/commit/3c7af383ff6b288c0e1093553e56a39d71b59e74))

The PyPI page showed broken images. Three reasons, all structural:

- README.md's hero and icon are relative paths (docs/*.svg). PyPI renders long_description
  standalone, so those resolve against pypi.org and 404. - They are SVG, and GitHub raw serves .svg
  as text/plain — an <img> would not render them even with an absolute URL. - The file carries 17
  repo-relative links (action.yml, LICENSE, .github/*, #anchors) that only resolve inside the repo.

README-pypi.md is the same project described with absolute https URLs only, and leads with install
  rather than hackathon framing. Its hero is the PNG the live site already serves — correct
  Content-Type, already deployed, zero new bytes in the repo.

Verified through readme_renderer (the renderer PyPI itself uses): 7 images survive sanitisation, all
  absolute https, no relative srcs; twine check passes on both wheel and sdist, and the sdist
  carries the file.

GitHub's README.md is unchanged — it keeps the animated SVG hero, which renders fine there.

### Documentation

- **site**: Audit landing + deck against what actually shipped
  ([`079c9c1`](https://github.com/edycutjong/armsmith/commit/079c9c14de475be726195c61873b0b88919f1681))

Both surfaces predated today's work and were quietly wrong in four ways.

- armsmith record appeared NOWHERE on either page, despite being the change that lets the probe
  rules run on a stranger's repo at all. The landing quickstart now shows record -> diagnose, and a
  new artifact card explains the manifest declares synthetic:false. - The deck counted 7 commands.
  There are 8. - Neither page linked PyPI or GitHub Releases. Added to the landing footer, the hero
  CTA row, and the deck's link slide; the deck numgrid gains a uvx tile. - The migration-template
  card still described the cards as anti-pattern + fix + citation. They now carry a paste-able
  before->after diff on 11 of 13, and the card says which two do not and why.

Also corrects 'add a 14th rule with zero core changes' to the truth: one YAML, one detector, one
  import.

Checked with a headless render of both pages: 0 console errors, 0 empty hrefs, no horizontal
  overflow.


## v1.2.0 (2026-08-09)

### Continuous Integration

- **release**: Link the pypi deployment to the package page
  ([`aacd397`](https://github.com/edycutjong/armsmith/commit/aacd3975a1f1ae55aa1d577c4fed901fa03c5c0d))

The Deployments panel showed a bare 'pypi' environment with nothing to click. environment.url points
  it at the published package, so the entry resolves to https://pypi.org/project/armsmith/.

Takes effect on the next publish; existing deployment records keep the url they were created with.

- **release**: Only publish to PyPI when a version was actually cut
  ([`024ce36`](https://github.com/edycutjong/armsmith/commit/024ce3648b55f036a7e474ea915ef5bd00a355e0))

The release workflow fires after every successful ci run, and publish-pypi was gated only on the
  PUBLISH_TO_PYPI variable — so it rebuilt and attempted a publish on every push regardless of
  whether semantic-release cut anything. Three publish attempts and three deployment records inside
  25 minutes, each a no-op saved only by skip-existing, and each one a deployment event.

The release job now exposes semantic-release's own 'released' output and publish-pypi requires it.
  Docs/chore/ci pushes stop touching PyPI entirely.

### Documentation

- Point the demo links at the re-cut video
  ([`fb1497d`](https://github.com/edycutjong/armsmith/commit/fb1497dd08dc0947b95b0cb8094d38e096f872c6))

Scene 6 of the demo was a replay-labelled -35% from the synthetic bundle. It is now the real
  measurement: the live A/B on a Neoverse-N2 runner, SDOT 0 -> 1, -86.5%, gate keep, every figure
  copied from the signed report-live.json CI artifact.

Replaced rather than inserted because the rules cap the video at three minutes and the cut was
  already 2:55.8. The new segment is frame-exact (497 frames, VO padded to the same 16.55s), so
  nothing downstream moved.

YouTube cannot swap the file on an existing upload, so the re-cut is a new id: vq15rK1iCww ->
  JsT83BYMWd0.

- **progress**: Record the shipped record command and the real test count
  ([`3ff5eee`](https://github.com/edycutjong/armsmith/commit/3ff5eee5537a626c8a0482cc64c8a6a5eb2623c5))

PROGRESS.md still said 234 tests (it is 410, at 100% line coverage) and still listed bundle
  recording as owed. armsmith record shipped on 2026-08-09; what remains of that item is ssh://
  targets only, so a laptop can record a bundle for a remote Arm box.

The Action Marketplace listing stays described as publish-pending — that one is still true. Only
  PyPI is published.

### Features

- **rules**: Put a real before/after diff in every actionable migration card
  ([`8109236`](https://github.com/edycutjong/armsmith/commit/8109236521e1b2e11ac5e2fe4e8cf798ae07b618))

The 13 cards were advertised as x86->Arm migration templates and as the rubric's reusable artifact,
  but grep '```' across all of them returned zero — they were 23 lines of prose each. An index of
  Arm Learning Path links is a useful thing, but it is not a template: you cannot paste a paragraph
  into a Dockerfile.

Rule descriptors now take optional before/after snippets plus a language to fence them in, and the
  export renders a 'Before -> after' section. The smallest honest edit for each anti-pattern, e.g.
  R12:

platforms: linux/amd64 platforms: linux/amd64,linux/arm64

11 of 13 rules carry one. R9 and R13 do not, on purpose: both are diagnostic — they tell you where
  the time is going and redirect the optimization, rather than naming a specific line to change.
  Inventing a snippet for them would be the same failure mode as inventing a measurement, so they
  render no fence at all.

The loader treats an empty string as absent, and a test asserts no rule ever ships half a pair — a
  'before' with no 'after' renders an anti-pattern the reader has no fix for.

414 tests, 100% coverage; counts synced across README, site, deck, CI and CONTRIBUTING.


## v1.1.0 (2026-08-09)

### Documentation

- Lead with the PyPI install now that armsmith 1.0.4 is published
  ([`15342ef`](https://github.com/edycutjong/armsmith/commit/15342efb2267b6188f2ba6b36523bf9837f0ed66))

armsmith is on PyPI, published from CI via Trusted Publishing (OIDC, no token in the repo), with
  sdist + wheel attached to the GitHub Release. Verified from a clean venv outside the repo: pip
  install armsmith and uvx armsmith both resolve, load all 13 rule packs from inside the wheel, and
  scan a repo they have never seen.

Adoption previously meant 'git clone + pip install -e', which filters out every drive-by user. Now:

uvx armsmith scan .

- README and site quickstarts lead with the zero-install one-liner, with the clone kept for the path
  that genuinely needs it: reproducing the gate against the replay bundles, which live in the repo.
  - Installation section splits 'to use it' from 'to hack on it'. - PyPI version badge in the top
  matter. - Reuse section names the install as a first-class artifact.

Held back until the package actually resolved — advertising an install command that does not work is
  the one claim this project cannot make.

### Features

- **record**: Capture a real replay bundle from the host you run on
  ([`a2e1871`](https://github.com/edycutjong/armsmith/commit/a2e1871e84178a280a1c2428b3c212ce5d31ec2d))

The largest gap in the tool: diagnose --replay was required, and no command produced a bundle. Ten
  of thirteen rules and the CI gate could only ever run against fixtures shipped in this repo, so a
  stranger got a three-rule static scan and nothing else. armsmith record closes that.

armsmith record . --out ./bundle --python .venv/bin/python armsmith diagnose --replay ./bundle

Captures what the host can honestly answer (lscpu, THP state, and the BLAS numpy reports for the
  interpreter you name) and copies in verbatim any real instrument output you already have:
  --build-log (R2), --pip-log (R8), --cmake-cache (R10), --gguf (R5), --perf (R9), --ort-session
  (R7), --llama-bench + --hyperfine (R13).

--python exists because R3 is a claim about the venv that serves YOUR model. Probing our own
  interpreter would answer the wrong question; armsmith does not even depend on numpy.

Honesty contract, enforced in code and asserted by tests: - manifest declares "synthetic": false —
  nothing here is invented - env and proc_maps are NEVER captured, so R6 cannot run from a recorded
  bundle and R11 stays half-fed. A bundle is published; an env block carries CI tokens and a maps
  dump carries host paths. - anything unobserved is omitted, not guessed; record prints which rules
  the bundle can and cannot answer before you run diagnose

Fixes a provenance bug this surfaced: build_report derived synthetic from mode ('replay' implied
  synthetic), conflating transport with provenance. A recorded bundle is replayed but real, and was
  being stamped synthetic — understating a genuine measurement as badly as the reverse would
  overstate one. The two are now separate axes and diagnose passes the manifest's own flag; the
  fallback keeps existing callers unchanged.

Verified end to end against a repo armsmith has never seen: recorded from
  huggingface/text-generation-inference, R3 ran on real numpy config and returned clean, unavailable
  probes skipped by name, report signed and VERIFY OK. 410 tests, 100% coverage.


## v1.0.4 (2026-08-09)

### Bug Fixes

- **release**: Attach the built sdist+wheel to the GitHub Release
  ([`99c77d1`](https://github.com/edycutjong/armsmith/commit/99c77d16b7219874f995985160b249d5398b3ef3))

v1.0.3 built armsmith-1.0.3.tar.gz and the wheel — the log says 'Successfully built' — and then
  shipped a Release page with zero downloads. upload_to_vcs_release is honoured by
  semantic-release's 'publish' command, not by 'version', and the action only runs 'version'. So the
  artifacts were produced and discarded.

Adds publish-action@v9, gated on released == 'true' so it is a no-op on runs that cut nothing. PyPI
  is unaffected either way: that job does its own build.


## v1.0.3 (2026-08-09)

### Bug Fixes

- **rules**: Stop R4 flagging integer arrays as float64 coercion
  ([`fbeccf2`](https://github.com/edycutjong/armsmith/commit/fbeccf2cca47de98071dea3c91f71bbbce0ef568))

R4 flagged any numpy constructor call missing dtype=, with no type inference at all. On a fresh
  clone of huggingface/text-generation-inference that produced 5 findings, 4 of them false — integer
  permutation arrays in the Marlin GPTQ path (gptq.py:461,463, util.py:134,136). Worse than noise:
  the fix it proposed was 'add dtype=np.float32', which applied to
  numpy.argsort(numpy.array([0,2,4,6,1,3,5,7])) would silently turn an index array into floats.

numpy infers dtype from the data. Verified against real numpy: np.array([0, 2, 4]) -> int64
  np.zeros(3) -> float64 np.array([[1,2],[3,4]]) -> int64 np.ones(3) -> float64 np.array([True,
  False]) -> bool np.empty(3) -> float64 np.array([1.0, 2]) -> float64 np.linspace(0,1)-> float64
  np.full(3, 0) -> int64 np.full(3, 0.5) -> float64

So R4 now reasons per constructor: zeros/ones/empty/linspace are float64 whatever you pass them and
  are always reported; array/full are reported only when the payload is not a provable integer
  literal (recursing through nested lists and unary +/-).

Anything unprovable stays flagged — np.array(x) with a non-literal argument is still reported,
  because under-reporting a real float64 coercion on an inference path costs more than one honest
  question. On TGI that leaves exactly one hit, segments.py:17, which is that conservative case.

TGI: 5 findings / 4 false -> 1 finding / 0 proven wrong. Documented in the README as a precision
  demo on a repo we have never seen, and pinned by
  test_r4_does_not_flag_an_integer_permutation_array. 386 tests, 100% cov.

Also aligns the README intro with the roadmap: PR rendering is dry-run and now says so in the first
  paragraph, not only 350 lines later.

### Continuous Integration

- **release**: Keep v1 floating, wire PyPI trusted publishing
  ([`61c5f5e`](https://github.com/edycutjong/armsmith/commit/61c5f5e5989df912326c727f9370d303fba07cd8))

Two adoption blockers, both packaging rather than code.

v1 never existed. README and action.yml both document 'uses: edycutjong/armsmith@v1', but only
  v1.0.0/v1.0.1/v1.0.2 were ever tagged, so github.com/edycutjong/armsmith/tree/v1 404s and a
  stranger copy-pasting the CI snippet gets 'Unable to resolve action'. The release job now
  force-moves v1 onto each new 1.x release, so the documented reference resolves and tracks the
  latest patch.

Nothing was installable. build_command was empty by design ('nothing is published to an index'), so
  adoption meant git clone + pip install -e. Now the release builds sdist+wheel and a publish job
  ships them to PyPI via Trusted Publishing — OIDC, no API token stored in the repo.

The publish job is gated on the PUBLISH_TO_PYPI repo variable and stays inert until the PyPI-side
  trusted publisher exists: a missing publisher would fail the run and paint the release red for
  what is purely account setup. Setup steps are in the job comment. The name 'armsmith' is free on
  PyPI (checked).

The README still says clone-and-install, and will keep saying it until a release actually lands on
  PyPI — advertising an install command that does not work yet is the exact kind of claim this
  project refuses to make.

Also drops 'opens PRs' from the package description, which is the text PyPI and GitHub both display;
  rendering is dry-run today.

### Documentation

- **readme**: Lift the fold — quickstart above, hedges below
  ([`12a32a8`](https://github.com/edycutjong/armsmith/commit/12a32a81a676a2622fc5d39ff8b00c658b97623e))

Getting Started sat at line ~294 of 444. A developer who wanted to run the thing scrolled past ~30
  badges, the problem statement, the architecture, the rigor tables and an honesty caveat before
  reaching a command.

- The four-line quickstart now sits directly under the badges, with the outputs it actually produces
  (386 passing, 4 kept / 2 dropped — both verified just now), plus pointers to make all and
  CONTRIBUTING, which the front door never mentioned. - python3, not python: macOS ships no bare
  'python', so the documented venv line failed on the first machine a judge is likely to use. -
  'Built with' and 'Quality gates' badge rows move down into Engineering Rigor, where they are
  evidence rather than an obstacle. Top matter keeps the CTA row, build status, and the Arm platform
  row. - The Status caveat moves below the Solution as 'What is measured, and what is replayed'. It
  is the right disclosure and the wrong first impression; the thesis line — the LLM plans, the
  silicon decides — now lands immediately after the pitch instead of behind a hedge.


## v1.0.2 (2026-08-09)

### Bug Fixes

- **schema**: Serve report.schema.json at the $id it declares
  ([`919ca52`](https://github.com/edycutjong/armsmith/commit/919ca52903428c7ba9eded331de0752b7c9476d1))

The repo-root schema/ path is a symlink to the packaged copy, which git stores as a symlink blob —
  so github.com and raw.githubusercontent both 404 on schema/report.schema.json even though the file
  is right there. Anyone told to 'build your own viewer against it' hit a dead link.

- README links now point at src/armsmith/schema/report.schema.json, which resolves for a browser and
  for curl. - site/schema/report.schema.json makes the declared $id
  (https://armsmith.edycu.dev/schema/report.schema.json) actually serve. A copy, not a symlink:
  Vercel will not follow one. - CI diffs the served copy against the packaged original, so the
  published schema can never drift from the one that validates reports.

The symlink stays — ci.yml reads through it.

### Documentation

- Link the badge at the live Devpost submission
  ([`779a195`](https://github.com/edycutjong/armsmith/commit/779a195e0601495c7dd50fe091c69ffb36c9b627))

Submitted to the Arm AI Optimization Challenge (Cloud AI) as
  https://devpost.com/software/armsmith-7j1lzt — verified live, and the page carries the repo,
  armsmith.edycu.dev and the demo video.

The Devpost badge now points at the submission itself rather than the hackathon landing page; the
  hackathon keeps its own badge beside it, relabelled to name the track. 'docs:' deliberately, so
  semantic-release does not cut a version for a badge.

- **contributing**: Fix the org in action.yml, document the detector contract
  ([`d7fc655`](https://github.com/edycutjong/armsmith/commit/d7fc655c55854e71032eb90f52889918d3a6db0a))

Three things a stranger hit immediately:

- action.yml's own usage comment said 'uses: edycu/armsmith@v1'. That org does not exist;
  copy-pasting it gets 'Unable to resolve action'. The README had it right, the file itself did not.
  - CONTRIBUTING told new contributors to expect 219 tests. It is 384, so the first checkpoint on
  the on-ramp failed. - 'Add a 14th rule with zero core changes' was not true: the detector also
  needs an import in detectors/__init__.py or the @register decorator never runs and the loader
  raises 'rules without detectors'. Now documented as one YAML + one detector + one import, with the
  real detect() signature spelled out — it was previously undocumented anywhere.

- **site**: Publish the measured Neoverse-N2 result, retire [PENDING]
  ([`97c356d`](https://github.com/edycutjong/armsmith/commit/97c356dea75e36ce331cecd3055a2de57a876ae7))

The site and deck were written before the live Arm leg existed and still said the hardware number
  was [PENDING] in seven places, while the README led with SDOT 0->1 and -86.5%. The two
  most-visited surfaces contradicted the headline and read as 'they never got real silicon'.

- Hero stat tile + figcaption now carry -86.5% / SDOT 0->1 on Neoverse-N2. - FAQ Q2 answers 'yes,
  one leg is real hardware' with the full table (0.059975s -> 0.008123s, band +/-0.000144s, gate
  keep) instead of [PENDING]. - Deck slide 09 moves the measurement into the 'real today' column and
  updates the CI matrix (8 jobs, 5 native arm64 — it claimed 6 and 2). - Stale counts corrected
  everywhere: 219 -> 384 tests, ~93% -> 100% coverage.

What stays unclaimed is stated more precisely than before: a throughput multiplier for YOUR model.
  The measured number is one int8 microkernel on one runner and is never extrapolated. The replay
  witness stays 0->4 — that is the synthetic fixture and is a different number from the live 0->1.


## v1.0.1 (2026-08-09)

### Bug Fixes

- **deck**: Stop slide 5's sub-labels overflowing their nodes
  ([`d5f724a`](https://github.com/edycutjong/armsmith/commit/d5f724acbb5e1fde961f94c9182faf65851a39c8))

Five labels on the trust-inversion diagram rendered outside their boxes, and 'evidence table' in the
  bot-PR node was clipped by the SVG viewBox itself — the last glyph was shaved off at 1280x720,
  1440x900 and 1920x1080, in both themes. Static geometry, so it reproduced on every navigation
  path.

At .diagram .b font-size 17.5px the six top-row nodes needed ~1671 user units plus gaps against 1780
  available; no redistribution of padding alone could close it. Dropped the dim sub-labels to 16px
  (the .t/.tg/.td/.lab classes are untouched) and retuned the top-row rect x/width, then re-anchored
  the seven edges and their labels to the new borders.

Geometry only: all 642 text nodes are byte-identical, no copy, colour or class changed. Every node
  now carries 25-28u left and 25-36u right padding.

The looping concern that prompted this pass was clean: 'rise' replays correctly on all 10 slides in
  both directions, forward and backward navigation are pixel-identical, and the caret's infinite
  blink sampled 220 times over 5.7 full periods showed only 0/1 with no stall. There is no analogue
  here of the 6s-SVG-blanking bug found in the demo video.

### Continuous Integration

- Add build + deploy-gate stages, and ship the schema in the wheel
  ([`ec1620c`](https://github.com/edycutjong/armsmith/commit/ec1620cc3fbf62c600739513a3bf0857e627c1b9))

Applying the enhance-project harness to a Python CLI. The Next.js-specific parts (Playwright,
  Lighthouse, bundle budget, layout metadata) do not apply; community health files and repo security
  were already at 100%.

Stage 3 (build verification) is new, and it found a real bug on its first run. Every other job
  installs with 'pip install -e .', which serves rule packs and the report schema straight out of
  the source tree. A wheel does not: package-data declared only rules/packs/*.yaml, and
  schema/report.schema.json lived OUTSIDE the package, so it could never ship. 'pip install
  armsmith' produced a CLI that imported fine, listed all 13 rules, and then raised
  FileNotFoundError the moment anything validated a report.

Fix: the schema is now canonical inside the package at armsmith/schema/, declared as package-data,
  and the repo root keeps a symlink so the public path judges and CI use still resolves - one file,
  no copy that can drift. schema_path() prefers the packaged copy and keeps the parent walk as a
  fallback, which is covered by a new test rather than left to chance.

The stage now proves it: build sdist+wheel, twine check, then install the wheel into a clean venv
  and run the CLI from OUTSIDE the repo so nothing on sys.path can mask a missing data file.

Also: Stage 6 deploy gate (one required check that means everything passed), stage labels on every
  job, and the stale '219 tests' label corrected to 384.

Dependabot moved to monthly + grouped + no majors. The old weekly/ungrouped/ majors-allowed config
  produced exactly its known failure mode here: four PRs unattended, one conflicted behind the
  others, one showing a stale red run. Security alerts are a separate channel and stay on.


## v1.0.0 (2026-08-09)

### Bug Fixes

- **seo**: Add a CTA to the OG image and cut the over-long descriptions
  ([`3413172`](https://github.com/edycutjong/armsmith/commit/34131724243bf95659a5e8e13e216ef5f9582358))

The link preview had no conversion affordance, so it read as a picture rather than somewhere to go.
  Adds a button-shaped 'Run the gate ->' with the domain beside it; headline drops 96px -> 88px
  (still inside the 88-112 spec range) to make room without pushing the tech pills off the canvas.

Descriptions were being truncated in the wild: index description 301 -> 147 (Google cuts ~155) index
  og:description 184 -> 108 (mobile previews cut ~125) deck description 218 -> 136 deck og/twitter
  135/131 -> 88

Also declares the real OG size: the markup claimed 2400x1260 while the file actually served was
  1200x630.

### Chores

- Ignore local venv, tool caches, and asset node_modules
  ([`a28234d`](https://github.com/edycutjong/armsmith/commit/a28234db6c05d86c9c035ca584c8fd60b570f1e7))

- Project harness — Makefile, .env.example, community health files
  ([`f9600c7`](https://github.com/edycutjong/armsmith/commit/f9600c72c5f3561472aaf5d2662f47d1f63d3538))

make test/lint/typecheck/e2e/ci-gate/cards/security. Contributing, security policy, code of conduct,
  issue and PR templates.

- **deps**: Bump actions/checkout from 4 to 7 ([#2](https://github.com/edycutjong/armsmith/pull/2),
  [`837f721`](https://github.com/edycutjong/armsmith/commit/837f721da7128027d4aab1b717c690ff8db2a2cb))

Bumps [actions/checkout](https://github.com/actions/checkout) from 4 to 7. - [Release
  notes](https://github.com/actions/checkout/releases) -
  [Changelog](https://github.com/actions/checkout/blob/main/CHANGELOG.md) -
  [Commits](https://github.com/actions/checkout/compare/v4...v7)

--- updated-dependencies: - dependency-name: actions/checkout dependency-version: '7'

dependency-type: direct:production

update-type: version-update:semver-major ...

Signed-off-by: dependabot[bot] <support@github.com>

Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>

- **deps**: Bump actions/setup-python from 5 to 7
  ([#1](https://github.com/edycutjong/armsmith/pull/1),
  [`d7cdf75`](https://github.com/edycutjong/armsmith/commit/d7cdf75076acf77bb560e9ef742fd026828932f2))

Bumps [actions/setup-python](https://github.com/actions/setup-python) from 5 to 7. - [Release
  notes](https://github.com/actions/setup-python/releases) -
  [Commits](https://github.com/actions/setup-python/compare/v5...v7)

--- updated-dependencies: - dependency-name: actions/setup-python dependency-version: '7'

dependency-type: direct:production

update-type: version-update:semver-major ...

Signed-off-by: dependabot[bot] <support@github.com>

Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>

- **deps**: Bump actions/upload-artifact from 4 to 7
  ([#4](https://github.com/edycutjong/armsmith/pull/4),
  [`ba1a428`](https://github.com/edycutjong/armsmith/commit/ba1a428dfeccccc9c09a0ad89f0138fa60a91bae))

Bumps [actions/upload-artifact](https://github.com/actions/upload-artifact) from 4 to 7. - [Release
  notes](https://github.com/actions/upload-artifact/releases) -
  [Commits](https://github.com/actions/upload-artifact/compare/v4...v7)

--- updated-dependencies: - dependency-name: actions/upload-artifact dependency-version: '7'

dependency-type: direct:production

update-type: version-update:semver-major ...

Signed-off-by: dependabot[bot] <support@github.com>

Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>

- **deps**: Bump github/codeql-action from 3 to 4
  ([#3](https://github.com/edycutjong/armsmith/pull/3),
  [`f001265`](https://github.com/edycutjong/armsmith/commit/f001265484f36a1bbc98de7f8b58de0b7292a719))

Bumps [github/codeql-action](https://github.com/github/codeql-action) from 3 to 4. - [Release
  notes](https://github.com/github/codeql-action/releases) -
  [Changelog](https://github.com/github/codeql-action/blob/main/CHANGELOG.md) -
  [Commits](https://github.com/github/codeql-action/compare/v3...v4)

--- updated-dependencies: - dependency-name: github/codeql-action dependency-version: '4'

dependency-type: direct:production

update-type: version-update:semver-major ...

Signed-off-by: dependabot[bot] <support@github.com>

Co-authored-by: dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>

- **site**: Deploy the landing page and deck straight from site/
  ([`4d7a70c`](https://github.com/edycutjong/armsmith/commit/4d7a70c04df117cbc44e2e405247b794632003a3))

Vercel is now git-connected to this repo, with vercel.json pinning the output directory to site/ —
  so the published site is the source in this repo, not a hand-synced copy in a second repo.

Drops site/CNAME: it only ever configured the GitHub Pages mirror, which this replaces.

### Continuous Integration

- Exclude the Lob detector from the secret scan (pytest-name false positives)
  ([`7d0403a`](https://github.com/edycutjong/armsmith/commit/7d0403a3b1be8503317c4336a9ecc3d600d02d78))

TruffleHog's Lob detector matches 'test_' + 35 chars, which is the shape of a pytest function name.
  It flagged 10 test names and zero credentials. Armsmith calls no Lob API, so the detector can only
  produce noise. Every other detector stays enabled and the scan still runs over full history.

- Run the reproduce gate on arm64 and publish a composite action
  ([`63eef46`](https://github.com/edycutjong/armsmith/commit/63eef4672edc4e5447df627119000c2ac2daace1))

Wires 'armsmith ci --replay' into the matrix as a drop-in Arm perf gate, adds a verified-secret
  TruffleHog scan over full history, and updates the suite count to 219. action.yml exposes the same
  gate as a composite action any repo can drop into its own workflow.

### Documentation

- Brand assets — icon, hero, and animated variants
  ([`4433939`](https://github.com/edycutjong/armsmith/commit/4433939875e249c5e4a4bb3007c5a668f553b393))

- Correct the coverage claim from ~93% to the measured 87%
  ([`f49dc0a`](https://github.com/edycutjong/armsmith/commit/f49dc0a2210fdfb04c3783c83eeadaed6bd73bc5))

Measured today: 234 passed, 1 skipped, 2315 statements, 291 missed = 87.4%. The README asserted ~93%
  in four places and pyproject repeated it in a comment.

The gap is mostly livebench.py, added yesterday in d132688 — 152 statements at 58%, the
  least-covered module in the tree. But even excluding it the figure is 89.5%, so ~93% was already
  stale before that landed.

This project's entire thesis is that it does not claim what it cannot prove. An unverified number in
  its own README is the one lie it cannot afford.

- Judge-facing README with a zero-hardware quickstart
  ([`9451d38`](https://github.com/edycutjong/armsmith/commit/9451d38d9f73976f5aff5e1ab2fc10605deb2627))

Leads with the two-minute path a reviewer can run on any x86 laptop, then the Arm64 setup, then
  reuse-and-extend. Every number labelled: no unlabelled figure in this repo is a hardware
  measurement.

- Normalize the README to the judge-facing pattern
  ([`e0039df`](https://github.com/edycutjong/armsmith/commit/e0039df6a0d51fe5cf94cb003d47d23bee3dca21))

Reorders into the canonical section sequence and adds the CTA badge row that was missing entirely
  (live site, pitch deck, Devpost) — all three targets verified 200. Swaps the hand-drawn "CI: arm64
  + x86" static badge for the real workflow badge, which goes red when CI actually breaks.

The four sections with no canonical slot are folded in as subheads rather than dropped: Trust Chain,
  Measured on Real Arm Silicon, and Honesty Notes now sit together under Engineering Rigor with a
  metrics table. "Full Setup on Arm64" becomes a proper Arm Integration section listing the six real
  Arm surfaces. Roadmap is generated from the actual TODO(S1) inventory.

No product screenshots exist, so "See it in Action" is omitted rather than faked, and the Demo Video
  CTA stays out until the video is uploaded.

Content preserved verbatim; a sentence-level diff caught three prose drops (the "most judges have no
  Graviton box" line, the native-arm64 diagnose command, and the live-capture/never-fabricates
  paragraph) and they are back.

- Point the public links at armsmith.edycu.dev
  ([`355553f`](https://github.com/edycutjong/armsmith/commit/355553f70109ffc272e83de34b8921c1b8fa1e9d))

The custom domain is live and its certificate is issued, so the README now matches what the page's
  own canonical/og:url have claimed all along. The vercel.app alias keeps working.

Also ignore report.json: 'armsmith diagnose' writes it into the cwd and it is a generated artifact,
  not a checked-in result.

- Publish the demo-video link on the README, landing page and deck
  ([`502b412`](https://github.com/edycutjong/armsmith/commit/502b412a11b0988356e7089f3eea038c3e67decf))

The video existed but nothing linked to it. The landing page still showed a DISABLED 'Demo video ·
  pending' button in the closing CTA row and a greyed-out footer item; the deck's link list ended in
  'demo video: [PENDING]'; the README had no reference at all.

The remaining [PENDING] tokens are left alone on purpose — they mark the live Graviton throughput
  multiplier, which genuinely is not measured yet.

- Report the live Arm measurement, and correct the honesty banner
  ([`9b78865`](https://github.com/edycutjong/armsmith/commit/9b788653ee5583d9bf4b0fe1bd0b2481443d6ec9))

The README's headline claim — "No number in this repository is a hardware measurement" — became
  false the moment bench-live landed. Replaces it with the distinction that is actually true and
  checkable: every number is either a labeled synthetic replay fixture or a real live measurement,
  and says which.

Adds "Measured on Real Arm Silicon" with the run from ubuntu-24.04-arm (Neoverse-N2, gcc 13.3.0):
  SDOT 0 → 1 in dot_i8, median kernel_s 0.059975s → 0.008123s, -86.5% against a ±0.000144s band,
  outputs hash-equal, gate keep. The witness row is the one that matters — ARMv8.0 has no
  dot-product instruction, so the baseline cannot contain one.

Also states the limits: one microbenchmark on one runner is proof the gate works on real silicon,
  not a claim about anyone's model.

Test count 219 → 234 throughout; PROGRESS/Tier-A table updated.

### Features

- 100% line coverage, semantic-release automation, v1.0.0
  ([`80da715`](https://github.com/edycutjong/armsmith/commit/80da7152bbf3e9f457c5ae928b2a4a54b7dca057))

Coverage 87% -> 100%: 2315 statements, 0 missed, across 383 passing tests (+149). Seven parallel
  sessions, one per module group, each writing a single new tests/test_cov_*.py:

cli.py 72.9% -> 100% (bench-live covered by faking only the OS boundary; the gate/witness/signing
  above it all still run for real) livebench.py 57.9% -> 100% (scripted fake host: compiler,
  objdump, lscpu, workload - no Arm hardware needed) r12_ci_matrix 70.7% -> 100% r13_divergence
  80.6% -> 100% (incl. the refuse-to-diagnose path) report.py 88.9% -> 100% (tamper tests RE-SIGN
  the tampered body, so hash+signature pass and only the recompute layer can catch it - proving that
  layer independently) probes.py 87.0% -> 100% gguf/keys/evidence/rules + base +
  r01/r02/r04/r05/r06/r07/r09/r11/gate/ fingerprint/diagnose/benchstats -> 100%

No pragma: no cover was added, no coverage config was touched, and no file under src/ was modified
  to make a line reachable. The number is earned.

Versioning: 0.1.0 -> 1.0.0, now automated by python-semantic-release off the Conventional Commit
  subjects this repo already uses. The release workflow chains off ci via workflow_run so a release
  can never be cut from a red build. It needs NO secrets - GITHUB_TOKEN with job-level contents:
  write is enough. test_cli.py's version assertion now reads __version__ instead of a literal, which
  would otherwise go red on every future release.

README: Arm Developer / Learning Paths / Graviton / KleidiAI / llama.cpp badges plus the stack and
  quality-gate rows, and a release badge.

- **cli**: Register scan, witness, pr and ci commands
  ([`5ab3889`](https://github.com/edycutjong/armsmith/commit/5ab3889d9b3967d8f9b915eb83713b5b11807031))

scan — static aarch64 anti-pattern scan (R1/R4/R12), no probes, no hardware. witness — before/after
  SDOT/UDOT/SMMLA/USMMLA counts from objdump text. pr — assemble the bot PR, one commit per KEPT fix
  (dry-run real). ci — reproduce-gate regression twin, exit-code contract.

Ships a signed report.json inside the scenario_ragserve replay bundle so 'armsmith verify' and the
  tamper demo work straight out of a clone. Adds 7 CLI tests (suite 208 -> 215); coverage note 94%
  -> 93%.

- **live**: Reproduce gate on real Arm silicon, not a replay
  ([`d132688`](https://github.com/edycutjong/armsmith/commit/d132688e427e6957993ddf35f5d7b2d2a42a0626))

Adds the first path in this codebase that produces a hardware measurement. armsmith bench-live
  compiles bench/int8_dot.c twice from one source, differing only in the -march flag rule R2 exists
  to flag:

baseline -O3 -march=armv8-a (ARMv8.0 — no dot-product unit) fix_R2 -O3 -march=armv8.2-a+dotprod
  (what R2 tells you to turn on)

then, on the host it is running on: reads a real lscpu for the ISA table, disassembles the dot_i8
  symbol in BOTH binaries and counts SDOT/UDOT/SMMLA/ USMMLA, ABAB-interleaves the timed runs
  through benchstats.plan_interleaved, and feeds the samples to the ordinary gate. Same
  refuse-to-claim-inside-the-noise-band rule as every replay bundle — a run that cannot beat its own
  noise is reported no_change and dropped.

LiveProbe stops being a stub for what it can observe honestly (lscpu, THP, harness-captured
  disassembly). It refuses env and proc_maps on purpose: a report is a published artifact and a CI
  environment block contains tokens. Every other probe kind still raises rather than guessing.

Honesty invariants, unit-tested: refuses to run off aarch64; live samples are never flagged
  synthetic; the two variants may differ only in the ISA flag.

CI gains a native arm64 live-bench job that runs this with --require-witness, verifies the signed
  report, and uploads it. 234 tests green.

- **rules**: Learning_path citations and rules export migration cards
  ([`8004aef`](https://github.com/edycutjong/armsmith/commit/8004aefd0ac7022d3c2c3215f9bb8a232e941a0d))

Every rule pack gains a learning_path field citing the Arm Learning Path it encodes (10 of 13 map
  upstream; R3/R8/R11 are upstream-only). 'rules export' emits 13 migration cards into
  docs/migration-templates/ so the rule corpus is reusable as documentation, not just as a scanner.

- **site**: Landing page and pitch deck for GitHub Pages
  ([`017e8e6`](https://github.com/edycutjong/armsmith/commit/017e8e69bcb3849f9a59f74a47fe3e32b025ff06))

Static, self-contained, no external dependencies. CNAME set to armsmith.edycu.dev.
