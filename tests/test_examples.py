"""Tests that the bundled ``examples/`` are real, runnable, and self-consistent.

These guard against doc-rot: the sample caption files must parse and round-trip,
the glossary must load, and both demo scripts must run to completion fully
offline (they use a ``ScriptedModel``, never a live LLM). If an example breaks,
CI fails here rather than a user discovering it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from subtitle_medic.glossary import load_glossary
from subtitle_medic.parser import dumps, loads
from subtitle_medic.rules import check

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SAMPLE_SRT = EXAMPLES / "sample.srt"
SAMPLE_VTT = EXAMPLES / "sample.vtt"
GLOSSARY = EXAMPLES / "glossary.txt"
CHECK_DEMO = EXAMPLES / "check_demo.py"
FIX_DEMO = EXAMPLES / "fix_demo.py"


# ---------------------------------------------------------------------------
# The data files exist and are well-formed
# ---------------------------------------------------------------------------


def test_example_files_exist():
    for path in (SAMPLE_SRT, SAMPLE_VTT, GLOSSARY, CHECK_DEMO, FIX_DEMO):
        assert path.is_file(), f"missing example: {path}"


def test_sample_srt_parses_and_roundtrips():
    text = SAMPLE_SRT.read_text(encoding="utf-8")
    subs = loads(text)
    assert len(subs) == 3
    # Re-parsing the serialized form yields an equal document (round-trip stable).
    assert loads(dumps(subs)) == subs


def test_sample_srt_is_structurally_clean_but_readability_flagged():
    # The SRT sample is intentionally clean on structure (so `fix` can emit it)
    # but trips readability rules (so there is something to correct/report).
    subs = loads(SAMPLE_SRT.read_text(encoding="utf-8"))
    report = check(subs)
    assert report.has_errors is False
    assert report.flagged_indices()  # at least one cue is flagged


def test_sample_vtt_parses_and_roundtrips():
    text = SAMPLE_VTT.read_text(encoding="utf-8")
    subs = loads(text)
    assert len(subs) == 3
    # WebVTT header + per-cue settings are preserved on round-trip.
    assert loads(dumps(subs)) == subs
    out = dumps(subs)
    assert out.startswith("WEBVTT")
    assert "align:start position:10%" in out


def test_glossary_loads_with_terms_and_mappings():
    gloss = load_glossary(str(GLOSSARY))
    assert gloss  # non-empty
    # An explicit mapping fixes the known machine-transcription misspelling.
    assert gloss.canonical_for("kuberntes") == "Kubernetes"
    # A bare canonical term is recognized case-insensitively.
    assert gloss.canonical_for("github") == "GitHub"


# ---------------------------------------------------------------------------
# The demo scripts run to completion (hermetic — no network, no live LLM)
# ---------------------------------------------------------------------------


def _run_demo(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("script", [CHECK_DEMO, FIX_DEMO], ids=["check_demo", "fix_demo"])
def test_demo_script_runs(script):
    result = _run_demo(script)
    assert result.returncode == 0, (
        f"{script.name} failed (rc={result.returncode})\n"
        f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    )
    assert result.stdout.strip(), f"{script.name} produced no output"


def test_check_demo_reports_violations():
    result = _run_demo(CHECK_DEMO)
    # The check demo prints the JSON the CLI would emit, including a violations key.
    assert '"violations"' in result.stdout
    assert "flagged cue indices" in result.stdout


def test_fix_demo_applies_and_stays_structurally_valid():
    result = _run_demo(FIX_DEMO)
    # The fix demo asserts structural validity internally; here we confirm it
    # reached the corrected-output stage and applied at least one edit.
    assert "Corrected SRT" in result.stdout
    assert "Kubernetes" in result.stdout
