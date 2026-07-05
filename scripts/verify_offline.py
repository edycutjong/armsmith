#!/usr/bin/env python3
"""verify_offline — prove the core loop is instrument-honest with zero network.

Runs the complete replay-mode pipeline end-to-end (COMPLEXITY §5):

    scan → deterministic plan → reproduce gate → sign → verify

against the bundled synthetic scenario, using a throwaway keypair, and exits
non-zero on any failure. No network, no hardware, no API keys.

    .venv/bin/python scripts/verify_offline.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from armsmith.diagnose import run_replay_diagnosis  # noqa: E402
from armsmith.evidence import render_markdown  # noqa: E402
from armsmith.keys import init_keys  # noqa: E402
from armsmith.report import verify_report, write_report  # noqa: E402


def main() -> int:
    bundle = ROOT / "fixtures" / "replays" / "scenario_ragserve"
    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="armsmith-offline-") as tmp:
        key_dir = Path(tmp) / "keys"
        init_keys(key_dir=key_dir)
        result = run_replay_diagnosis(bundle, key_dir=key_dir, sign=True)
        rpt = result.report

        def check(name: str, ok: bool) -> None:
            print(f"[{'ok' if ok else 'FAIL'}] {name}")
            if not ok:
                failures.append(name)

        check("report is replay-mode + synthetic-labeled",
              rpt["mode"] == "replay" and rpt["synthetic"] is True)
        matched = sum(1 for f in rpt["findings"] if f["status"] == "matched")
        check(f"rule scan matched {matched} planted flaws (expect 7)", matched == 7)
        kept = [f for f in rpt["fixes"] if f["verdict"] == "keep"]
        dropped = [f for f in rpt["fixes"] if f["verdict"] == "drop"]
        check(f"gate kept {len(kept)} (expect 4)", len(kept) == 4)
        check(f"gate dropped {len(dropped)} with reasons (expect 2)",
              len(dropped) == 2 and all(f["reasons"] for f in dropped))
        check("report signed", result.signed)

        verified = verify_report(rpt)
        check("verify: hash + signature + schema + recompute", verified.ok)
        for issue in verified.issues:
            print(f"       issue: {issue.kind}: {issue.detail}")

        md = render_markdown(rpt, specs_by_id=result.specs)
        check("evidence markdown carries replay banner", "REPLAY MODE" in md)
        check("evidence markdown reports drops", "dropped by the gate" in md)

        out = Path(tmp) / "report.json"
        write_report(rpt, out)
        check("report round-trips to disk", out.stat().st_size > 1000)

    if failures:
        print(f"\nverify_offline: {len(failures)} FAILURE(S): {failures}")
        return 1
    print("\nverify_offline: ALL CHECKS PASSED — the loop runs honest and offline")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
