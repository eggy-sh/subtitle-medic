"""Tests for Markdown + JSON rendering of check and correction results."""

from __future__ import annotations

import json

from subtitle_medic.corrector import Corrector
from subtitle_medic.glossary import parse_glossary
from subtitle_medic.parser import loads
from subtitle_medic.report import (
    check_report_json,
    check_report_markdown,
    correction_report_json,
    correction_report_markdown,
)
from subtitle_medic.rules import check

# --- check report ----------------------------------------------------------


def test_check_report_json_structure_stable(broken_srt):
    report = check(loads(broken_srt))
    payload = check_report_json(report, source="broken.srt")
    assert payload["source"] == "broken.srt"
    assert "summary" in payload
    assert "errors" in payload["summary"]
    assert "warnings" in payload["summary"]
    assert isinstance(payload["violations"], list)
    first = payload["violations"][0]
    assert "rule" in first
    assert "severity" in first
    assert "cue_index" in first
    # Serializable.
    assert json.dumps(payload)


def test_check_report_json_clean(valid_srt):
    report = check(loads(valid_srt))
    payload = check_report_json(report)
    assert payload["ok"] is True
    assert payload["summary"]["errors"] == 0
    assert payload["violations"] == []


def test_check_report_markdown_has_heading_summary_and_rows(broken_srt):
    report = check(loads(broken_srt))
    md = check_report_markdown(report, source="broken.srt")
    assert md.startswith("# Caption QA report")
    assert "broken.srt" in md
    assert "**Summary:**" in md
    # One table row per violation.
    rows = [line for line in md.splitlines() if line.startswith("| ") and "---" not in line]
    # header row + N violation rows; subtract the header row.
    assert len(rows) - 1 == len(report.violations)


def test_check_report_markdown_clean_says_no_violations(valid_srt):
    report = check(loads(valid_srt))
    md = check_report_markdown(report)
    assert "No violations found." in md
    assert "PASS" in md


# --- correction report -----------------------------------------------------


def _result(broken_srt):
    subs = loads(broken_srt)
    gl = parse_glossary("kuberntes => Kubernetes\nKubernetes\nGitHub\n")
    return Corrector(model=None, glossary=gl).correct(subs)


def test_correction_report_json_includes_edits_residual_and_telemetry(broken_srt):
    result = _result(broken_srt)
    payload = correction_report_json(result, source="broken.srt")
    assert payload["source"] == "broken.srt"
    assert payload["used_model"] is False
    assert isinstance(payload["edits"], list)
    edit = payload["edits"][0]
    assert "before" in edit
    assert "after" in edit
    # Post-correction residual violations carried through.
    assert payload["post_report"] is not None
    assert "violations" in payload["post_report"]
    # Telemetry present (empty for no-LLM mode but serializable).
    assert "telemetry" in payload
    assert json.dumps(payload)


def test_correction_report_markdown_renders_before_after(broken_srt):
    result = _result(broken_srt)
    md = correction_report_markdown(result, source="broken.srt")
    assert md.startswith("# Caption correction report")
    assert "broken.srt" in md
    assert "Before:" in md
    assert "After:" in md
    assert "## Residual violations after correction" in md


def test_correction_report_markdown_no_edits():
    subs = loads("1\n00:00:01,000 --> 00:00:03,000\nClean cue.\n")
    result = Corrector(model=None).correct(subs)
    md = correction_report_markdown(result)
    assert "No cues required correction." in md


def test_correction_report_markdown_without_post_report_or_telemetry():
    from subtitle_medic.corrector import CorrectionResult

    subs = loads("1\n00:00:01,000 --> 00:00:03,000\nClean cue.\n")
    # Minimal result with no post_report and no telemetry exercises the
    # "section omitted" branches.
    result = CorrectionResult(subtitles=subs, edits=[], post_report=None, telemetry=None)
    md = correction_report_markdown(result)
    assert "Residual violations" not in md
    assert "Telemetry" not in md
    payload = correction_report_json(result)
    assert payload["post_report"] is None
    assert payload["telemetry"] is None


def test_correction_report_json_serializable_with_llm(broken_srt):
    from replykit import ScriptedModel

    subs = loads(broken_srt)
    model = ScriptedModel(
        [
            "@reply name=edit_cue\ntext = Kubernetes and GitHub.\n@end",
            "done",
            "@reply name=edit_cue\ntext = Oops.\n@end",
            "done",
        ]
    )
    model.model = "claude-opus-4-8"
    result = Corrector(model=model, glossary=parse_glossary("Kubernetes\nGitHub\n")).correct(subs)
    payload = correction_report_json(result)
    assert payload["used_model"] is True
    assert payload["telemetry"]["calls"] > 0
    assert json.dumps(payload)
    md = correction_report_markdown(result)
    assert "## Telemetry" in md
