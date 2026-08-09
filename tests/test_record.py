"""`armsmith record` — writing a REAL bundle from the host it runs on.

The point of these tests is the honesty contract, not just the happy path:
a recorded bundle must declare `"synthetic": false`, must never contain the
refused probes, and must omit — never invent — anything it could not observe.
"""

import json
import subprocess

import pytest

from armsmith import record as rec
from armsmith.probes import LIVE_REFUSED, PROBE_KINDS, ReplayProbe, load_manifest


class _FakeLive:
    """LiveProbe stand-in: macOS has no lscpu or THP, so the captured branch
    is unreachable on the dev machine without this."""

    def __init__(self, available: dict[str, str]):
        self._available = available

    def has(self, kind: str) -> bool:
        return kind in self._available

    def text(self, kind: str) -> str:
        return self._available[kind]


@pytest.fixture
def repo(tmp_path):
    d = tmp_path / "myrepo"
    (d / ".git").mkdir(parents=True)
    (d / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (d / "app.py").write_text("import numpy as np\nx = np.zeros(8)\n")
    return d


def _no_numpy(monkeypatch):
    monkeypatch.setattr(
        rec, "_capture_numpy_show_config", lambda python=None: (None, "numpy absent")
    )


def _fake_live(monkeypatch, available):
    monkeypatch.setattr(rec, "LiveProbe", lambda: _FakeLive(available))


# --- the honesty contract ----------------------------------------------------

def test_recorded_bundle_declares_itself_real_not_synthetic(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"lscpu": "Model name: Neoverse-N2\n"})
    out = tmp_path / "b"

    res = rec.record_bundle(repo, out)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["synthetic"] is False
    assert manifest["mode"] == "live"
    assert "lscpu" in manifest["captured_probes"]
    # load_manifest is the gate every consumer goes through
    assert load_manifest(out).synthetic is False
    assert res.captured_kinds == ["lscpu"]


def test_refused_probes_are_never_written(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"lscpu": "x", "thp": "always [madvise] never\n"})

    rec.record_bundle(repo, tmp_path / "b")

    for kind in LIVE_REFUSED:
        assert not (tmp_path / "b" / "probes" / PROBE_KINDS[kind]).exists()
    reasons = {c.kind: c.reason for c in rec.record_bundle(repo, tmp_path / "c").captures}
    for kind in LIVE_REFUSED:
        assert reasons[kind].startswith("refused —")


def test_unobservable_probes_are_omitted_with_a_reason_not_invented(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})  # nothing observable

    res = rec.record_bundle(repo, tmp_path / "b")

    assert res.captured_kinds == []
    probes_dir = tmp_path / "b" / "probes"
    assert list(probes_dir.iterdir()) == []          # nothing fabricated
    assert json.loads((tmp_path / "b" / "manifest.json").read_text())["captured_probes"] == []
    assert all(c.reason for c in res.captures if not c.captured)


# --- capture paths -----------------------------------------------------------

def test_numpy_capture_success_unlocks_r3(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(
        rec, "_capture_numpy_show_config",
        lambda python=None: ("name: openblas64\n", "py -c ..."),
    )
    _fake_live(monkeypatch, {})

    res = rec.record_bundle(repo, tmp_path / "b")

    assert (tmp_path / "b" / "probes" / "numpy_show_config.txt").read_text() == "name: openblas64\n"
    assert "R3" in res.rules_enabled


@pytest.mark.parametrize(
    "returncode,stdout,expect_reason",
    [(1, "", "not importable"), (0, "   \n", "no output")],
)
def test_numpy_capture_failure_modes(monkeypatch, returncode, stdout, expect_reason):
    monkeypatch.setattr(
        rec.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, returncode, stdout, ""),
    )
    text, reason = rec._capture_numpy_show_config()
    assert text is None and expect_reason in reason


def test_numpy_capture_returns_the_real_config_text(monkeypatch):
    monkeypatch.setattr(
        rec.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, "Build Dependencies:\n  name: openblas64\n", ""),
    )
    text, source = rec._capture_numpy_show_config()
    assert "openblas64" in text
    assert "numpy.show_config()" in source


def test_numpy_capture_reports_a_missing_interpreter(tmp_path):
    text, reason = rec._capture_numpy_show_config(str(tmp_path / "no-such-python"))
    assert text is None and "interpreter not found" in reason


def test_numpy_capture_reports_an_unrunnable_interpreter(monkeypatch, tmp_path):
    broken = tmp_path / "python"
    broken.write_text("not an executable")
    monkeypatch.setattr(rec.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("Exec format error")))
    text, reason = rec._capture_numpy_show_config(str(broken))
    assert text is None and "could not run" in reason


# --- ingesting real artifacts the operator already has -----------------------

def test_ingested_artifact_is_copied_verbatim(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})
    log = tmp_path / "build.log"
    log.write_text("gcc -O3 -march=armv8-a foo.c\n")

    res = rec.record_bundle(repo, tmp_path / "b", ingest={"build_log": log})

    assert (tmp_path / "b" / "probes" / "build_log.txt").read_text() == log.read_text()
    # R2 requires build_log AND lscpu. With only the build log captured it must
    # NOT be listed as answerable — claiming otherwise would overstate what the
    # bundle can diagnose, which is the one thing this tool must not do.
    assert "R2" not in res.rules_enabled


def test_a_multi_probe_rule_is_enabled_only_when_every_probe_is_present(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    log = tmp_path / "build.log"
    log.write_text("gcc -O3 -march=armv8-a foo.c\n")

    _fake_live(monkeypatch, {})                       # no lscpu
    without = rec.record_bundle(repo, tmp_path / "b1", ingest={"build_log": log})
    assert "R2" not in without.rules_enabled

    _fake_live(monkeypatch, {"lscpu": "Model name: Neoverse-N2\n"})
    with_both = rec.record_bundle(repo, tmp_path / "b2", ingest={"build_log": log})
    assert "R2" in with_both.rules_enabled          # both probes present


def test_missing_ingest_file_is_reported_not_fatal(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})

    res = rec.record_bundle(repo, tmp_path / "b", ingest={"build_log": tmp_path / "nope.log"})

    cap = next(c for c in res.captures if c.kind == "build_log")
    assert not cap.captured and "file not found" in cap.reason


def test_unknown_ingest_kind_is_rejected(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})
    with pytest.raises(ValueError, match="unknown probe kind"):
        rec.record_bundle(repo, tmp_path / "b", ingest={"telepathy": tmp_path})


# --- two-probe rules ---------------------------------------------------------

def test_r11_needs_both_thp_and_proc_maps_and_proc_maps_is_refused(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"thp": "always [madvise] never\n"})

    res = rec.record_bundle(repo, tmp_path / "b")

    assert "thp" in res.captured_kinds
    # proc_maps is refused on purpose, so R11 can never be enabled by record.
    assert "R11" not in res.rules_enabled


def test_r13_needs_both_instruments(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})
    lb, hf = tmp_path / "lb.json", tmp_path / "hf.json"
    lb.write_text("{}")
    hf.write_text("{}")

    only_one = rec.record_bundle(repo, tmp_path / "b1", ingest={"llama_bench": lb})
    assert "R13" not in only_one.rules_enabled

    both = rec.record_bundle(repo, tmp_path / "b2", ingest={"llama_bench": lb, "hyperfine": hf})
    assert "R13" in both.rules_enabled


# --- the repo copy -----------------------------------------------------------

def test_repo_is_copied_without_git_or_venv(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})

    res = rec.record_bundle(repo, tmp_path / "b")

    assert res.repo_copied
    assert (tmp_path / "b" / "repo" / "app.py").is_file()
    assert not (tmp_path / "b" / "repo" / ".git").exists()


def test_repo_copy_is_idempotent(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})
    rec.record_bundle(repo, tmp_path / "b")
    rec.record_bundle(repo, tmp_path / "b")           # exercises the rmtree branch
    assert (tmp_path / "b" / "repo" / "app.py").is_file()


def test_repo_copy_can_be_skipped_and_repo_can_be_omitted(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})

    a = rec.record_bundle(repo, tmp_path / "a", copy_repo=False)
    assert not a.repo_copied and not (tmp_path / "a" / "repo").exists()

    b = rec.record_bundle(None, tmp_path / "b")
    assert not b.repo_copied
    assert b.scenario == "b"                          # falls back to the out-dir name


def test_scenario_and_note_reach_the_manifest(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})

    rec.record_bundle(repo, tmp_path / "b", scenario="prod-inference", note="captured on c8g")

    m = json.loads((tmp_path / "b" / "manifest.json").read_text())
    assert m["scenario"] == "prod-inference" and m["note"] == "captured on c8g"


# --- the whole point: the bundle is consumable -------------------------------

def test_recorded_bundle_is_readable_by_replayprobe_and_diagnose(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"lscpu": "Model name: Neoverse-N2\nFlags: asimddp\n"})
    out = tmp_path / "b"
    rec.record_bundle(repo, out, scenario="live-capture")

    probe = ReplayProbe(out)
    assert probe.manifest.synthetic is False
    assert "Neoverse-N2" in probe.text("lscpu")

    from armsmith.diagnose import run_replay_diagnosis
    result = run_replay_diagnosis(out, sign=False)
    # Provenance must survive into the report: replayed transport, REAL data.
    assert result.report["mode"] == "replay"
    assert result.report["synthetic"] is False


def test_result_separates_captured_from_missing_kinds(tmp_path, repo, monkeypatch):
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"lscpu": "x"})

    res = rec.record_bundle(repo, tmp_path / "b")

    assert res.captured_kinds == ["lscpu"]
    # everything refused or unobservable shows up as missing, with no overlap
    assert set(res.missing_kinds) >= set(LIVE_REFUSED)
    assert not set(res.captured_kinds) & set(res.missing_kinds)


def test_capture_result_exposes_the_rules_it_unlocks():
    assert rec.CaptureResult("build_log", True, "x").rules == ("R2",)
    assert rec.CaptureResult("lscpu", True, "x").rules == ()


# --- provenance must survive into every surface ------------------------------

def test_recorded_bundle_is_not_labelled_synthetic_anywhere(tmp_path, repo, monkeypatch):
    """The mirror-image overclaim: stamping REAL data as synthetic.

    A bundle from `armsmith record` is replayed but genuine. The CLI banner and
    the PR body both used to key off transport (`mode == "replay"`) and called
    it synthetic, which understates a real measurement as badly as the reverse
    would overstate one.
    """
    from armsmith.diagnose import run_replay_diagnosis
    from armsmith.evidence import render_markdown

    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {"lscpu": "Model name: Neoverse-N2\n"})
    out = tmp_path / "b"
    rec.record_bundle(repo, out, scenario="live-capture")

    report = run_replay_diagnosis(out, sign=False).report
    assert report["synthetic"] is False
    assert report["mode"] == "replay"

    body = render_markdown(report)
    assert "SYNTHETIC DATA" not in body
    assert "RECORDED — REAL OBSERVATIONS" in body
    assert '"synthetic": false' in body


def test_synthetic_fixture_is_still_labelled_synthetic(tmp_path):
    """The guard must not swing the other way."""
    import json

    from tests.conftest import FIXTURES

    from armsmith.evidence import render_markdown

    report = json.loads((FIXTURES / "replays" / "scenario_ragserve" / "report.json").read_text())
    assert report["synthetic"] is True
    body = render_markdown(report)
    assert "SYNTHETIC DATA" in body


def test_recording_into_a_directory_inside_the_repo_does_not_recurse(tmp_path, monkeypatch):
    """The documented invocation is `armsmith record . --out ./armsmith-bundle`.

    The bundle therefore lives inside the tree being copied, and copytree will
    descend into the directory it is writing until the interpreter gives up.
    This reproduced as a RecursionError on CI.
    """
    _no_numpy(monkeypatch)
    _fake_live(monkeypatch, {})

    repo = tmp_path / "proj"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "a.py").write_text("import numpy as np\nx = np.zeros(4)\n")

    res = rec.record_bundle(repo, repo / "armsmith-bundle")     # inside the repo

    assert res.repo_copied
    assert (repo / "armsmith-bundle" / "repo" / "pkg" / "a.py").is_file()
    # the bundle must not have copied itself
    assert not list((repo / "armsmith-bundle" / "repo").rglob("armsmith-bundle"))
