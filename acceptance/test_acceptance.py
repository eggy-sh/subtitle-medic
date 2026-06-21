"""Hermetic, reproducible acceptance runner for subtitle-medic.

Executes EVERY scenario from ``acceptance/scenarios.md`` (SC-01 .. SC-10) under
identical conditions and asserts each machine-checkable success criterion. No
network and no live LLM are ever touched: the only model used is replykit's
hermetic :class:`~replykit.ScriptedModel` (SC-05), and every CLI scenario runs
in the deterministic no-LLM / rules-only path. No new model calls are introduced
by this runner.

Two ways to run, both identical in what they assert:

* ``.venv/bin/pytest acceptance/test_acceptance.py`` — runs the scenarios as
  pytest test functions (one per scenario).
* ``.venv/bin/python acceptance/test_acceptance.py`` — runs every scenario
  directly and writes ``acceptance/EVIDENCE.md`` capturing, per scenario: the id,
  the exact command/inputs, a captured output / artifact summary, and a PASS/FAIL
  verdict. Exit code is 0 iff all scenarios pass.

Each scenario is a function returning ``(command_or_inputs, evidence_summary)``
and asserting its criteria inline; the module-level harness wraps each call so a
failed assertion is recorded (not fatal) when generating evidence, and re-raised
as a normal test failure under pytest.

The CLI is invoked exactly as a user would: ``.venv/bin/subtitle-medic ...`` via
a subprocess, so exit codes and the single-JSON-object stdout contract are tested
end to end. Library scenarios (SC-05, SC-07) drive the public package surface.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Paths and the hermetic invocation contract.
# --------------------------------------------------------------------------- #

REPO = Path(__file__).resolve().parent.parent
VENV_BIN = REPO / ".venv" / "bin"
CLI = VENV_BIN / "subtitle-medic"
PY = VENV_BIN / "python"
EXAMPLES = REPO / "examples"
SAMPLE_SRT = EXAMPLES / "sample.srt"
SAMPLE_VTT = EXAMPLES / "sample.vtt"
GLOSSARY = EXAMPLES / "glossary.txt"
EVIDENCE_PATH = Path(__file__).resolve().parent / "EVIDENCE.md"

# A hermetic environment: scrub provider API keys so even an accidental live
# call would have nothing to authenticate with. The CLI scenarios run no-LLM and
# SC-05 uses ScriptedModel, so no network is ever attempted.
HERMETIC_ENV = {
    k: v
    for k, v in os.environ.items()
    if k not in {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OLLAMA_HOST"}
}


def run_cli(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run the installed ``subtitle-medic`` CLI with the hermetic environment."""
    return subprocess.run(
        [str(CLI), *args],
        capture_output=True,
        text=True,
        env=HERMETIC_ENV,
        cwd=str(REPO),
    )


def only_json(stdout: str) -> dict[str, Any]:
    """Assert stdout is exactly one JSON object and nothing else, and return it.

    This enforces the ``--json`` contract: a single parseable object, no banner
    lines, no trailing prose. ``json.loads`` over the whole stripped stdout fails
    if any non-JSON text is present.
    """
    text = stdout.strip()
    obj = json.loads(text)  # raises if not exactly one JSON value
    assert isinstance(obj, dict), f"expected a JSON object, got {type(obj).__name__}"
    return obj


# --------------------------------------------------------------------------- #
# Scenario implementations. Each returns (command/inputs, evidence_summary) and
# raises AssertionError on any unmet criterion.
# --------------------------------------------------------------------------- #


def sc_01() -> tuple[str, str]:
    """SC-01 — structural + readability QA; --json contract; exit codes."""
    cmd = f"{CLI} check {SAMPLE_SRT} --json"
    proc = run_cli(["check", str(SAMPLE_SRT), "--json"])
    assert proc.returncode == 0, f"exit code {proc.returncode} != 0; stderr={proc.stderr!r}"
    obj = only_json(proc.stdout)
    assert obj["has_errors"] is False, obj
    assert obj["cue_count"] == 3, obj
    assert obj["summary"]["errors"] == 0, obj
    assert obj["summary"]["warnings"] == 3, obj
    assert obj["summary"]["total"] == 3, obj
    assert obj["flagged_indices"] == [1, 3], obj

    viols = obj["violations"]
    want = [
        ("max_duration", 1, 8000, 7000),
        ("cpl", 1, 99, 42),
        ("min_duration", 3, 300, 700),
    ]
    got = [(v["rule"], v["cue_index"], v["observed"], v["limit"]) for v in viols]
    assert got == want, f"violations mismatch: {got} != {want}"
    assert all(v["severity"] == "warning" for v in viols), viols

    summary = (
        f"exit=0; has_errors=false; cue_count=3; warnings=3; flagged=[1,3]; "
        f"violations={got}; all severity 'warning'."
    )
    return cmd, summary


def sc_02() -> tuple[str, str]:
    """SC-02 — structural invariants; exit-code-1 contract."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "out_of_order.srt"
        # Cue 2 (1.000-2.000) starts before cue 1 (5.000-8.000).
        path.write_text(
            "1\n00:00:05,000 --> 00:00:08,000\nFirst cue.\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nSecond cue is out of order here.\n",
            encoding="utf-8",
        )
        cmd = f"{CLI} check {path} --json  (cue 2 starts before cue 1)"
        proc = run_cli(["check", str(path), "--json"])
        assert proc.returncode == 1, f"exit {proc.returncode} != 1; stderr={proc.stderr!r}"
        obj = only_json(proc.stdout)
        assert obj["has_errors"] is True, obj
        assert obj["ok"] is False, obj
        assert obj["summary"]["errors"] == 2, obj
        viols = obj["violations"]
        by_rule = {v["rule"]: v for v in viols}
        order = by_rule.get("timing_order")
        overlap = by_rule.get("timing_overlap")
        assert order is not None, f"no timing_order violation: {viols}"
        assert overlap is not None, f"no timing_overlap violation: {viols}"
        assert (order["cue_index"], order["observed"], order["limit"], order["severity"]) == (
            2,
            1000,
            5000,
            "error",
        ), order
        assert (
            overlap["cue_index"],
            overlap["observed"],
            overlap["limit"],
            overlap["severity"],
        ) == (2, 1000, 8000, "error"), overlap
        summary = (
            "exit=1; has_errors=true; ok=false; errors=2; "
            "timing_order(cue 2, observed 1000, limit 5000, error); "
            "timing_overlap(cue 2, observed 1000, limit 8000, error)."
        )
        return cmd, summary


def sc_03() -> tuple[str, str]:
    """SC-03 — configurable readability thresholds; flag plumbing."""
    cmd = f"{CLI} check {SAMPLE_VTT} --max-cpl 10 --json"
    proc = run_cli(["check", str(SAMPLE_VTT), "--max-cpl", "10", "--json"])
    assert proc.returncode == 0, f"exit {proc.returncode} != 0; stderr={proc.stderr!r}"
    obj = only_json(proc.stdout)
    assert obj["ok"] is False, obj
    assert obj["has_errors"] is False, obj
    assert obj["summary"]["warnings"] == 3, obj
    assert obj["summary"]["errors"] == 0, obj
    viols = obj["violations"]
    assert viols, "expected cpl violations"
    assert all(v["rule"] == "cpl" for v in viols), viols
    assert all(v["severity"] == "warning" for v in viols), viols
    summary = (
        f"exit=0; ok=false; has_errors=false; warnings=3; errors=0; "
        f"all {len(viols)} violations rule=='cpl' severity=='warning'."
    )
    return cmd, summary


def sc_04() -> tuple[str, str]:
    """SC-04 — deterministic no-LLM correction; glossary; artifacts; telemetry."""
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "out.srt"
        qa = Path(d) / "qa.md"
        cmd = f"{CLI} fix {SAMPLE_SRT} --glossary {GLOSSARY} -o {out} --report {qa} --json"
        proc = run_cli(
            [
                "fix",
                str(SAMPLE_SRT),
                "--glossary",
                str(GLOSSARY),
                "-o",
                str(out),
                "--report",
                str(qa),
                "--json",
            ]
        )
        assert proc.returncode == 0, f"exit {proc.returncode} != 0; stderr={proc.stderr!r}"
        obj = only_json(proc.stdout)
        assert obj["used_model"] is False, obj
        assert obj["applied_count"] == 1, obj
        assert obj["structurally_valid"] is True, obj
        assert obj["ok"] is True, obj

        cue1 = next(e for e in obj["edits"] if e["cue_index"] == 1)
        assert cue1["changed"] is True, cue1
        after0 = cue1["after"][0]
        assert "Kubernetes" in after0, after0
        assert "GitHub" in after0, after0
        assert "kuberntes" not in after0, after0
        assert "github" not in after0, after0  # no lowercase 'github'

        tel = obj["telemetry"]
        assert tel["calls"] == 0, tel
        assert tel["total_cost_usd"] == 0, tel

        # Re-check the written file.
        assert out.exists(), "out.srt was not written"
        recheck = run_cli(["check", str(out), "--json"])
        robj = only_json(recheck.stdout)
        assert robj["cue_count"] == 3, robj
        assert robj["has_errors"] is False, robj

        # Cue-1 timing line must be byte-identical to input.
        written = out.read_text(encoding="utf-8")
        assert "00:00:01,000 --> 00:00:09,000" in written, written

        # Markdown report artifact exists and carries the heading.
        assert qa.exists(), "qa.md was not written"
        qa_text = qa.read_text(encoding="utf-8")
        assert "# Caption correction report" in qa_text, qa_text[:120]

        summary = (
            "exit=0; used_model=false; applied_count=1; structurally_valid=true; ok=true; "
            f"cue1.after[0]={after0!r} (has Kubernetes+GitHub, no 'kuberntes'/'github'); "
            "telemetry.calls=0, total_cost_usd=0; "
            "recheck out.srt cue_count=3 has_errors=false; "
            "timing line '00:00:01,000 --> 00:00:09,000' preserved byte-identical; "
            "qa.md contains '# Caption correction report'."
        )
        return cmd, summary


def sc_05() -> tuple[str, str]:
    """SC-05 — model-mode cue-scoped correction via hermetic ScriptedModel (rubric)."""
    from replykit import ScriptedModel

    from subtitle_medic.corrector import Corrector
    from subtitle_medic.glossary import load_glossary
    from subtitle_medic.parser import dumps, loads

    text = SAMPLE_SRT.read_text(encoding="utf-8")
    glossary = load_glossary(str(GLOSSARY))
    # '\\n' is a literal backslash-n in the protocol text; the edit_cue tool turns
    # it back into a hard line break. Flagged cues are 1 and 3.
    cue1_fixed = "We deployed to Kubernetes and the GitHub\\naction ran cleanly."
    cue3_fixed = "Oops."
    scripted = ScriptedModel(
        [
            f'@reply name=edit_cue\ntext = "{cue1_fixed}"\n@end',
            "Done.",
            f'@reply name=edit_cue\ntext = "{cue3_fixed}"\n@end',
            "Done.",
        ]
    )
    result = Corrector(scripted, glossary=glossary).correct(loads(text))
    cues = result.subtitles.cues

    checks: list[tuple[str, bool]] = [
        ("used_model is True", result.used_model is True),
        ("applied_count == 2", result.applied_count == 2),
        ("structurally_valid is True", result.structurally_valid is True),
        ("len(cues) == 3", len(cues) == 3),
        ("cue1 start_ms == 1000", cues[0].start_ms == 1000),
        ("cue1 end_ms == 9000", cues[0].end_ms == 9000),
        ("cue3 lines == ['Oops.']", cues[2].lines == ["Oops."]),
        ("telemetry calls > 0", result.telemetry.as_dict()["calls"] > 0),
        (
            "round-trip lines equal",
            [c.lines for c in loads(dumps(result.subtitles)).cues]
            == [c.lines for c in result.subtitles.cues],
        ),
    ]
    passed = sum(1 for _, ok in checks if ok)
    total = len(checks)
    score = passed / total
    failed = [name for name, ok in checks if not ok]
    assert score == 1.0, f"score {score:.3f} < 1.0; failed: {failed}"
    cmd = (
        "library: Corrector(ScriptedModel([edit_cue cue1, 'Done.', edit_cue cue3, 'Done.']), "
        f"glossary).correct(loads(open({SAMPLE_SRT}).read()))"
    )
    summary = (
        f"rubric score {passed}/{total} == 1.0; used_model=True; applied_count=2; "
        f"structurally_valid=True; len(cues)=3; cue1 timing (1000,9000) unchanged; "
        f"cue3 lines=['Oops.']; telemetry.calls={result.telemetry.as_dict()['calls']}>0; "
        "round-trip lines equal."
    )
    return cmd, summary


def sc_06() -> tuple[str, str]:
    """SC-06 — hard structural emit gate; exit-code-1 on fix; nothing written."""
    with tempfile.TemporaryDirectory() as d:
        broken = Path(d) / "broken.srt"
        out = Path(d) / "o.srt"
        broken.write_text(
            "1\n00:00:05,000 --> 00:00:08,000\nFirst cue.\n\n"
            "2\n00:00:01,000 --> 00:00:02,000\nSecond cue is out of order here.\n",
            encoding="utf-8",
        )
        cmd = f"{CLI} fix {broken} -o {out} --json  (out-of-order SRT from SC-02)"
        proc = run_cli(["fix", str(broken), "-o", str(out), "--json"])
        assert proc.returncode == 1, f"exit {proc.returncode} != 1; stderr={proc.stderr!r}"
        obj = only_json(proc.stdout)
        assert obj["structurally_valid"] is False, obj
        assert obj["ok"] is False, obj
        assert "structural invariant" in obj["error"], obj["error"]
        assert not out.exists(), f"output {out} should NOT have been created"
        summary = (
            "exit=1; structurally_valid=false; ok=false; "
            f"error contains 'structural invariant' ({obj['error']!r}); "
            "output o.srt NOT created."
        )
        return cmd, summary


def sc_07() -> tuple[str, str]:
    """SC-07 — format auto-detect; round-trip fidelity; VTT header/NOTE/settings."""
    from subtitle_medic.model import CaptionFormat
    from subtitle_medic.parser import detect_format, dumps, loads

    body = SAMPLE_VTT.read_text(encoding="utf-8")
    subs = loads(body)
    out = dumps(subs)
    subs2 = loads(out)

    assert detect_format(body) is CaptionFormat.VTT, detect_format(body)
    assert subs.header == subs2.header, (subs.header, subs2.header)
    assert any("Kind: captions" in h for h in subs.header), subs.header
    assert any(h.startswith("NOTE") for h in subs.header), subs.header
    assert subs.cues[1].settings == "align:start position:10%", subs.cues[1].settings
    assert "align:start position:10%" in out, out

    t1 = [(c.start_ms, c.end_ms, tuple(c.lines), c.settings) for c in subs.cues]
    t2 = [(c.start_ms, c.end_ms, tuple(c.lines), c.settings) for c in subs2.cues]
    assert t1 == t2, (t1, t2)

    cmd = "library: subs=loads(open(sample.vtt).read()); out=dumps(subs); subs2=loads(out)"
    summary = (
        "detect_format==CaptionFormat.VTT; header equal across round-trip and contains "
        "'Kind: captions' + a 'NOTE' line; cues[1].settings=='align:start position:10%' "
        "appears in out; per-cue (start_ms,end_ms,lines,settings) tuples equal."
    )
    return cmd, summary


def sc_08() -> tuple[str, str]:
    """SC-08 — auto-detection on clean VTT; no-LLM fix no-op pass; exit codes."""
    # check baseline
    cmd_check = f"{CLI} check {SAMPLE_VTT} --json"
    pc = run_cli(["check", str(SAMPLE_VTT), "--json"])
    assert pc.returncode == 0, f"check exit {pc.returncode} != 0; stderr={pc.stderr!r}"
    cobj = only_json(pc.stdout)
    assert cobj["ok"] is True, cobj
    assert cobj["has_errors"] is False, cobj
    assert cobj["cue_count"] == 3, cobj
    assert cobj["summary"]["total"] == 0, cobj

    # fix no-op (no glossary, no model)
    cmd_fix = f"{CLI} fix {SAMPLE_VTT} --json"
    pf = run_cli(["fix", str(SAMPLE_VTT), "--json"])
    assert pf.returncode == 0, f"fix exit {pf.returncode} != 0; stderr={pf.stderr!r}"
    fobj = only_json(pf.stdout)
    assert fobj["applied_count"] == 0, fobj
    assert fobj["structurally_valid"] is True, fobj
    assert fobj["used_model"] is False, fobj
    assert fobj["ok"] is True, fobj

    cmd = f"{cmd_check}  AND  {cmd_fix}"
    summary = (
        "check: exit=0, ok=true, has_errors=false, cue_count=3, total=0. "
        "fix: exit=0, applied_count=0, structurally_valid=true, used_model=false, ok=true."
    )
    return cmd, summary


def sc_09() -> tuple[str, str]:
    """SC-09 — ParseError surfacing; exit-code-2; --json failure object."""
    with tempfile.TemporaryDirectory() as d:
        bad = Path(d) / "bad.srt"
        # Single-dash '->' instead of '-->'.
        bad.write_text("1\n00:00:01,000 -> 00:00:02,000\nBad arrow line.\n", encoding="utf-8")
        cmd = f"{CLI} check {bad} --json  (timing uses '->' not '-->')"
        proc = run_cli(["check", str(bad), "--json"])
        assert proc.returncode == 2, f"exit {proc.returncode} != 2; stderr={proc.stderr!r}"
        obj = only_json(proc.stdout)  # also asserts: nothing but one JSON object
        assert obj["ok"] is False, obj
        err = obj["error"]
        assert isinstance(err, str), obj
        assert "could not parse" in err, err
        assert "-->" in err, err
        summary = (
            "exit=2; single JSON object on stdout; ok=false; "
            f"error contains 'could not parse' and '-->' ({err!r})."
        )
        return cmd, summary


def sc_10() -> tuple[str, str]:
    """SC-10 — usage errors: missing file + bad provider; exit-code-2; failure object."""
    # (a) missing file
    cmd_a = f"{CLI} check /no/such/file.srt --json"
    pa = run_cli(["check", "/no/such/file.srt", "--json"])
    assert pa.returncode == 2, f"(a) exit {pa.returncode} != 2; stderr={pa.stderr!r}"
    aobj = only_json(pa.stdout)
    assert aobj["ok"] is False, aobj
    assert "no such file" in aobj["error"], aobj["error"]

    # (b) bad provider name; stdout must be the single error object only.
    cmd_b = f"{CLI} fix {SAMPLE_SRT} --model bogus --json"
    pb = run_cli(["fix", str(SAMPLE_SRT), "--model", "bogus", "--json"])
    assert pb.returncode == 2, f"(b) exit {pb.returncode} != 2; stderr={pb.stderr!r}"
    bobj = only_json(pb.stdout)
    assert bobj["ok"] is False, bobj
    berr = bobj["error"]
    assert "unknown --model" in berr, berr
    assert "anthropic" in berr and "openai" in berr and "ollama" in berr, berr
    # "nothing written": the error object is the only key set, no output/report wrote.

    cmd = f"(a) {cmd_a}   (b) {cmd_b}"
    summary = (
        "(a) exit=2; ok=false; error contains 'no such file'. "
        "(b) exit=2; ok=false; error contains 'unknown --model' and lists "
        "anthropic/openai/ollama; stdout is the single error object only."
    )
    return cmd, summary


# --------------------------------------------------------------------------- #
# Registry + harness.
# --------------------------------------------------------------------------- #

SCENARIOS: list[tuple[str, str, Callable[[], tuple[str, str]]]] = [
    ("SC-01", "Structural + readability QA; --json contract; exit codes", sc_01),
    ("SC-02", "Structural invariants; exit-code-1 contract", sc_02),
    ("SC-03", "Configurable readability thresholds; flag plumbing", sc_03),
    ("SC-04", "Deterministic no-LLM correction; glossary; artifacts; telemetry", sc_04),
    ("SC-05", "Model-mode cue-scoped correction via hermetic ScriptedModel", sc_05),
    ("SC-06", "Hard structural emit gate; exit-1 on fix; nothing written", sc_06),
    ("SC-07", "Format auto-detect; round-trip fidelity; VTT preservation", sc_07),
    ("SC-08", "Auto-detection on clean VTT; no-LLM fix no-op pass; exit codes", sc_08),
    ("SC-09", "ParseError surfacing; exit-code-2; --json failure object", sc_09),
    ("SC-10", "Usage errors (I/O + bad provider); exit-code-2; failure object", sc_10),
]


# Pytest entry points: one test per scenario, asserting under identical conditions.
def _make_test(fn: Callable[[], tuple[str, str]]):
    def _test() -> None:
        fn()

    return _test


test_sc_01 = _make_test(sc_01)
test_sc_02 = _make_test(sc_02)
test_sc_03 = _make_test(sc_03)
test_sc_04 = _make_test(sc_04)
test_sc_05 = _make_test(sc_05)
test_sc_06 = _make_test(sc_06)
test_sc_07 = _make_test(sc_07)
test_sc_08 = _make_test(sc_08)
test_sc_09 = _make_test(sc_09)
test_sc_10 = _make_test(sc_10)


def _run_all_and_write_evidence() -> int:
    """Run every scenario, write EVIDENCE.md, and return a process exit code."""
    rows: list[dict[str, Any]] = []
    for sid, capability, fn in SCENARIOS:
        try:
            cmd, summary = fn()
            rows.append(
                {
                    "id": sid,
                    "capability": capability,
                    "command": cmd,
                    "summary": summary,
                    "verdict": "PASS",
                    "reason": "",
                }
            )
        except Exception as exc:  # noqa: BLE001 — record, do not abort the suite.
            rows.append(
                {
                    "id": sid,
                    "capability": capability,
                    "command": locals().get("cmd", "(command not captured)"),
                    "summary": "",
                    "verdict": "FAIL",
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )

    passed = sum(1 for r in rows if r["verdict"] == "PASS")
    total = len(rows)

    lines: list[str] = []
    lines.append("# subtitle-medic — acceptance evidence")
    lines.append("")
    lines.append(
        "Hermetic, reproducible acceptance run. No network and no live LLM: CLI "
        "scenarios run the deterministic no-LLM path; SC-05 uses replykit's "
        "`ScriptedModel`. All scenarios executed under identical conditions by "
        "`acceptance/test_acceptance.py`."
    )
    lines.append("")
    lines.append(f"- Repository: `{REPO}`")
    lines.append(f"- Interpreter: `{PY}`")
    lines.append(f"- CLI under test: `{CLI}`")
    lines.append(
        "- Hermetic env: provider API keys "
        "(`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`OLLAMA_HOST`) scrubbed."
    )
    lines.append("")
    lines.append(f"## Result: {passed}/{total} scenarios PASS")
    lines.append("")
    lines.append("| Scenario | Verdict | Capability |")
    lines.append("| --- | --- | --- |")
    for r in rows:
        lines.append(f"| {r['id']} | {r['verdict']} | {r['capability']} |")
    lines.append("")

    for r in rows:
        lines.append(f"### {r['id']} — {r['verdict']}")
        lines.append("")
        lines.append(f"**Capability:** {r['capability']}")
        lines.append("")
        lines.append("**Command / inputs:**")
        lines.append("")
        lines.append("```")
        lines.append(str(r["command"]))
        lines.append("```")
        lines.append("")
        if r["verdict"] == "PASS":
            lines.append("**Captured output / artifact summary:**")
            lines.append("")
            lines.append(r["summary"])
        else:
            lines.append("**Failure reason:**")
            lines.append("")
            lines.append(f"`{r['reason']}`")
        lines.append("")

    EVIDENCE_PATH.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")

    # Console echo for the orchestrator.
    print(f"Wrote evidence to {EVIDENCE_PATH}")
    print(f"Result: {passed}/{total} PASS")
    for r in rows:
        if r["verdict"] == "FAIL":
            print(f"  FAIL {r['id']}: {r['reason']}")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(_run_all_and_write_evidence())
