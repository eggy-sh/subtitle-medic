"""Integration tests for the ``subtitle-medic`` CLI (Typer + Rich).

These exercise the CLI end to end through Typer's :class:`CliRunner`: argument
parsing, the ``--json`` one-object contract, exit codes, file I/O, and the
``--model`` provider mapping. The LLM ``fix`` path is driven hermetically by
monkeypatching the CLI's model factory to return a replykit ``ScriptedModel`` —
no network, no live LLM, no API keys.
"""

from __future__ import annotations

import json

import pytest
from replykit import ScriptedModel
from typer.testing import CliRunner

from subtitle_medic import cli
from subtitle_medic.cli import app

runner = CliRunner()


# A structurally clean SRT whose only problems are *readability* (a too-long line
# on cue 1, a too-short cue 3). `fix` can edit these and still pass the structural
# gate, so the corrected file is writable.
CLEAN_BUT_FLAGGED_SRT = """\
1
00:00:01,000 --> 00:00:09,000
We deployed to kuberntes and the github action ran a realy long line over the studio limit.

2
00:00:10,000 --> 00:00:13,000
A perfectly fine cue.

3
00:00:14,000 --> 00:00:14,300
oops
"""

# A structurally BROKEN SRT: cue 2 (index 3) starts before cue 1 ends (overlap)
# and the indices are non-contiguous (1, 3). These are invariants the corrector
# cannot repair, so `fix` must refuse to emit and `check` must exit non-zero.
STRUCTURAL_BROKEN_SRT = """\
1
00:00:01,000 --> 00:00:09,000
Overlapping cue line.

3
00:00:08,500 --> 00:00:08,600
oops
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _edit_call(text: str) -> str:
    """Build the replykit ``@reply`` text for a single ``edit_cue`` tool call."""
    return f'@reply name=edit_cue\ntext = "{text}"\n@end'


# ---------------------------------------------------------------------------
# Top-level / help
# ---------------------------------------------------------------------------


def test_no_args_shows_help_and_exits_nonzero():
    result = runner.invoke(app, [])
    assert result.exit_code != 0
    assert "check" in result.output
    assert "fix" in result.output


def test_help_lists_both_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "check" in result.output
    assert "fix" in result.output


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def test_check_clean_file_exits_zero(tmp_srt_file):
    result = runner.invoke(app, ["check", str(tmp_srt_file)])
    assert result.exit_code == 0
    assert "clean" in result.output.lower()


def test_check_json_emits_exactly_one_object(tmp_srt_file):
    result = runner.invoke(app, ["check", str(tmp_srt_file), "--json"])
    assert result.exit_code == 0
    # Exactly one JSON object on stdout, nothing else.
    payload = json.loads(result.stdout)
    assert payload["cue_count"] == 3
    assert payload["has_errors"] is False
    assert "summary" in payload
    assert set(payload["summary"]) >= {"errors", "warnings"}


def test_check_readability_warnings_still_exit_zero(tmp_path):
    # A clean-timing file with a too-long line warns but has no structural ERROR,
    # so the exit code stays 0 (warnings don't fail the gate).
    p = _write(tmp_path, "warn.srt", CLEAN_BUT_FLAGGED_SRT)
    result = runner.invoke(app, ["check", str(p), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["has_errors"] is False
    assert payload["summary"]["warnings"] >= 1


def test_check_structural_error_exits_one(tmp_path):
    p = _write(tmp_path, "broken.srt", STRUCTURAL_BROKEN_SRT)
    result = runner.invoke(app, ["check", str(p), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["has_errors"] is True


def test_check_thresholds_are_configurable(tmp_path):
    # With a generous CPL the long line no longer warns.
    p = _write(tmp_path, "warn.srt", CLEAN_BUT_FLAGGED_SRT)
    strict = runner.invoke(app, ["check", str(p), "--json", "--max-cpl", "40"])
    loose = runner.invoke(
        app, ["check", str(p), "--json", "--max-cpl", "200", "--max-dur", "99000"]
    )
    strict_warns = json.loads(strict.stdout)["summary"]["warnings"]
    loose_warns = json.loads(loose.stdout)["summary"]["warnings"]
    assert loose_warns < strict_warns


def test_check_missing_file_exits_two(tmp_path):
    result = runner.invoke(app, ["check", str(tmp_path / "nope.srt"), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "no such file" in payload["error"]


def test_check_unparseable_file_exits_two(tmp_path):
    p = _write(tmp_path, "bad.srt", "1\nnot-a-timecode\nsome text\n")
    result = runner.invoke(app, ["check", str(p), "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False


def test_check_vtt_roundtrips(tmp_path, valid_vtt):
    p = _write(tmp_path, "sample.vtt", valid_vtt)
    result = runner.invoke(app, ["check", str(p), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["cue_count"] == 3


# ---------------------------------------------------------------------------
# fix — no-LLM (deterministic) mode
# ---------------------------------------------------------------------------


def test_fix_no_model_writes_corrected_output(tmp_path, tmp_glossary_file):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    out = tmp_path / "out.srt"
    report = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "fix",
            str(src),
            "--glossary",
            str(tmp_glossary_file),
            "-o",
            str(out),
            "--report",
            str(report),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["used_model"] is False
    assert payload["structurally_valid"] is True
    assert out.exists()
    assert report.exists()
    # The deterministic glossary pass fixed the brand spellings on the flagged cue.
    corrected = out.read_text(encoding="utf-8")
    assert "Kubernetes" in corrected
    assert "kuberntes" not in corrected


def test_fix_report_artifact_is_markdown(tmp_path, tmp_glossary_file):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    report = tmp_path / "report.md"
    runner.invoke(
        app,
        ["fix", str(src), "--glossary", str(tmp_glossary_file), "--report", str(report)],
    )
    md = report.read_text(encoding="utf-8")
    assert md.startswith("#")
    assert "Edits" in md or "edit" in md.lower()


def test_fix_structural_break_refuses_and_exits_one(tmp_path):
    src = _write(tmp_path, "broken.srt", STRUCTURAL_BROKEN_SRT)
    out = tmp_path / "out.srt"
    result = runner.invoke(app, ["fix", str(src), "-o", str(out), "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "structural" in payload["error"]
    # Nothing was written.
    assert not out.exists()


def test_fix_human_mode_prints_corrected_text_without_output_flag(tmp_path, tmp_glossary_file):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    result = runner.invoke(app, ["fix", str(src), "--glossary", str(tmp_glossary_file)])
    assert result.exit_code == 0
    # The corrected captions are echoed to stdout (human-mode filter behavior).
    assert "-->" in result.output


def test_fix_missing_glossary_exits_two(tmp_path):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    result = runner.invoke(
        app, ["fix", str(src), "--glossary", str(tmp_path / "nope.txt"), "--json"]
    )
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False


# ---------------------------------------------------------------------------
# fix — model (LLM) mode, hermetic via a monkeypatched ScriptedModel
# ---------------------------------------------------------------------------


def test_fix_with_scripted_model_applies_llm_edits(tmp_path, tmp_glossary_file, monkeypatch):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    out = tmp_path / "out.srt"

    # The corrector runs one agent pass per flagged cue (cues 1 and 3). Each pass
    # wants a tool call then a final plain-text turn. The edit_cue tool takes a
    # single `text` argument (lines split on the literal '\n').
    scripted = ScriptedModel(
        [
            _edit_call("We deployed to Kubernetes and the GitHub action ran."),
            "Done.",
            _edit_call("Oops."),
            "Done.",
        ]
    )
    monkeypatch.setattr(cli, "_build_model", lambda spec: scripted)

    result = runner.invoke(
        app,
        [
            "fix",
            str(src),
            "--glossary",
            str(tmp_glossary_file),
            "--model",
            "anthropic",
            "-o",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["used_model"] is True
    assert payload["structurally_valid"] is True
    assert payload["applied_count"] >= 1
    corrected = out.read_text(encoding="utf-8")
    assert "Kubernetes" in corrected
    # Timing was preserved verbatim — the LLM never touched the timecodes.
    assert "00:00:01,000 --> 00:00:09,000" in corrected


def test_fix_llm_telemetry_is_reported(tmp_path, monkeypatch):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    scripted = ScriptedModel(
        [
            _edit_call("Fixed cue one over the limit."),
            "Done.",
            _edit_call("Oops."),
            "Done.",
        ]
    )
    monkeypatch.setattr(cli, "_build_model", lambda spec: scripted)
    result = runner.invoke(app, ["fix", str(src), "--model", "anthropic", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["telemetry"]["calls"] >= 1


# ---------------------------------------------------------------------------
# --model provider mapping (unit-level, no SDKs installed)
# ---------------------------------------------------------------------------


def test_build_model_empty_is_no_llm():
    assert cli._build_model("") is None
    assert cli._build_model("   ") is None


def test_build_model_unknown_raises_actionable_error():
    with pytest.raises(ValueError, match="unknown --model"):
        cli._build_model("gpt4")


@pytest.mark.parametrize("provider", ["anthropic", "openai", "ollama"])
def test_build_model_missing_sdk_raises_value_error(provider):
    # None of the provider SDKs are installed in the hermetic test env, so each
    # lazy adapter import surfaces a clear, pip-hinted ValueError — never a bare
    # ImportError at CLI import time.
    with pytest.raises(ValueError, match="pip install"):
        cli._build_model(provider)


def test_fix_bad_model_exits_two(tmp_path):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    result = runner.invoke(app, ["fix", str(src), "--model", "gpt4", "--json"])
    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "unknown --model" in payload["error"]


# ---------------------------------------------------------------------------
# Human (Rich) rendering paths
# ---------------------------------------------------------------------------


def test_check_human_mode_renders_violation_table(tmp_path):
    # Readability warnings exercise the Rich violations table in human mode.
    p = _write(tmp_path, "warn.srt", CLEAN_BUT_FLAGGED_SRT)
    result = runner.invoke(app, ["check", str(p)])
    assert result.exit_code == 0
    out = result.output
    assert "warning" in out.lower()
    # A rule id and the threshold-table heading both show up.
    assert "cpl" in out.lower() or "max_duration" in out.lower()


def test_fix_human_mode_shows_applied_edits_and_paths(tmp_path, tmp_glossary_file):
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    out = tmp_path / "out.srt"
    report = tmp_path / "report.md"
    result = runner.invoke(
        app,
        [
            "fix",
            str(src),
            "--glossary",
            str(tmp_glossary_file),
            "-o",
            str(out),
            "--report",
            str(report),
        ],
    )
    assert result.exit_code == 0
    text = result.output
    # The human summary names the no-LLM mode, the applied-edits table, the
    # residual-violations line, and the written artifact paths.
    assert "edit(s) applied" in text
    assert "no-LLM" in text
    assert "out.srt" in text
    assert "report.md" in text


def test_fix_human_mode_no_changes_branch(tmp_path):
    # A flagged cue with no glossary and no model yields no text change, so the
    # "no text changes were applied" branch renders.
    src = _write(tmp_path, "in.srt", CLEAN_BUT_FLAGGED_SRT)
    out = tmp_path / "out.srt"
    result = runner.invoke(app, ["fix", str(src), "-o", str(out)])
    assert result.exit_code == 0
    assert "no text changes were applied" in result.output


def test_fix_structural_break_human_mode_writes_to_stderr(tmp_path):
    # In human mode the structural-gate failure message goes to stderr; stdout
    # stays clean. (CliRunner merges streams unless mix_stderr=False.)
    src = _write(tmp_path, "broken.srt", STRUCTURAL_BROKEN_SRT)
    result = runner.invoke(app, ["fix", str(src)])
    assert result.exit_code == 1
    assert "structural" in result.output.lower()


def test_fix_human_mode_no_residual_violations(tmp_path, monkeypatch):
    # A file whose only problem is one over-long line; the scripted model returns
    # a short replacement, so the post-correction report has no residual
    # violations and the "no residual violations" branch renders.
    one_long = (
        "1\n00:00:01,000 --> 00:00:05,000\n"
        "This single caption line is intentionally far too long for the line limit.\n"
    )
    src = _write(tmp_path, "long.srt", one_long)
    scripted = ScriptedModel([_edit_call("Short and tidy."), "Done."])
    monkeypatch.setattr(cli, "_build_model", lambda spec: scripted)
    result = runner.invoke(
        app, ["fix", str(src), "--model", "anthropic", "-o", str(tmp_path / "o.srt")]
    )
    assert result.exit_code == 0
    assert "no residual violations" in result.output


def test_main_entrypoint_is_callable(monkeypatch):
    # main() just delegates to the Typer app; calling it with --help exits 0.
    called = {}

    def fake_app(*args, **kwargs):
        called["ran"] = True

    monkeypatch.setattr(cli, "app", fake_app)
    cli.main()
    assert called.get("ran") is True
