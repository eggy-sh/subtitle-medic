"""Tests for the cue-scoped corrector. The invariant tests are the crown jewels.

Hermetic: corrections are driven only by replykit ScriptedModel / MockModel.
"""

from __future__ import annotations

import json

from replykit import MockModel, ScriptedModel, Telemetry

from subtitle_medic.corrector import EDIT_TOOL_NAME, Corrector, CueEdit
from subtitle_medic.glossary import parse_glossary
from subtitle_medic.model import CaptionFormat, Cue, Subtitles
from subtitle_medic.parser import loads
from subtitle_medic.rules import structural_violations


def _scripted(*responses, model_name=None):
    script = ScriptedModel(list(responses))
    if model_name is not None:
        script.model = model_name
    return script


def _edit_block(text):
    return f"@reply name=edit_cue\ntext = {text}\n@end"


# --- (1) valid edit applied, timing/index/count IDENTICAL ------------------


def test_scripted_edit_applied_timing_and_count_preserved(broken_srt):
    subs = loads(broken_srt)
    gl = parse_glossary("Kubernetes\nGitHub\n")
    # Flagged cues are 1 and 3; supply an edit then a final answer for each.
    model = _scripted(
        _edit_block("We deployed to Kubernetes and the GitHub action ran a line."),
        "done",
        _edit_block("Oops."),
        "done",
    )
    result = Corrector(model=model, glossary=gl).correct(subs)

    assert result.used_model is True
    assert result.applied_count >= 1
    # Same cue count.
    assert len(result.subtitles) == len(subs)
    # Per-cue timing + index UNCHANGED, field by field.
    for before, after in zip(subs.cues, result.subtitles.cues, strict=True):
        assert after.start_ms == before.start_ms
        assert after.end_ms == before.end_ms
        assert after.index == before.index
    # before/after recorded on the edits.
    applied = [e for e in result.edits if e.applied]
    assert applied
    assert applied[0].before != applied[0].after


# --- (2) glossary typo enforced --------------------------------------------


def test_glossary_typo_enforced_in_output(broken_srt):
    subs = loads(broken_srt)
    gl = parse_glossary("kuberntes => Kubernetes\nKubernetes\nGitHub\n")
    model = _scripted(
        _edit_block("We deployed to Kubernetes and the GitHub action ran."),
        "done",
        _edit_block("Oops."),
        "done",
    )
    result = Corrector(model=model, glossary=gl).correct(subs)
    assert "Kubernetes" in result.subtitles.cues[0].text
    assert "kuberntes" not in result.subtitles.cues[0].text


# --- (3) more/fewer cues impossible by construction ------------------------


def test_single_edit_tool_no_timing_args():
    corrector = Corrector(model=MockModel(""))
    registry, holder = corrector._build_registry()
    # Exactly one tool.
    assert len(registry) == 1
    spec = registry.specs()[0]
    assert spec.name == EDIT_TOOL_NAME
    # Timing/index are not parameters of the only tool.
    arg_names = {a.name for a in spec.args}
    assert "start_ms" not in arg_names
    assert "end_ms" not in arg_names
    assert "index" not in arg_names
    assert arg_names == {"text"}
    assert holder["lines"] is None


def test_correct_cue_count_never_changes_even_with_extra_text(broken_srt):
    subs = loads(broken_srt)
    # Model emits text with embedded newlines (more "lines") — count stays same.
    model = _scripted(
        _edit_block("a\\nb\\nc\\nd"),
        "done",
        _edit_block("x"),
        "done",
    )
    result = Corrector(model=model).correct(subs)
    assert len(result.subtitles) == len(subs)


# --- (4) re-validation gate ------------------------------------------------


def test_revalidation_gate_keeps_structural_invariants(broken_srt):
    subs = loads(broken_srt)
    # Even a pathological edit can only touch lines; structural signatures of the
    # output never grow beyond the input's pre-existing breakage.
    model = _scripted(
        _edit_block(""),  # empty text
        "done",
        _edit_block("\\n\\n\\n"),  # only newlines
        "done",
    )
    result = Corrector(model=model).correct(subs)
    # The corrector never *introduces* a structural violation: the set of
    # structural signatures in the output is a subset of the input's.
    before_sigs = {(v.rule, v.cue_index) for v in structural_violations(subs)}
    after_sigs = {(v.rule, v.cue_index) for v in structural_violations(result.subtitles)}
    assert after_sigs <= before_sigs


def test_structurally_valid_true_on_clean_input(valid_srt):
    subs = loads(valid_srt)
    # Clean input has no flagged cues, so nothing is edited and it stays valid.
    result = Corrector(model=None).correct(subs)
    assert result.structurally_valid is True


def test_edit_on_clean_doc_stays_structurally_valid():
    # A clean two-cue doc with one flagged cue (over CPL).
    subs = Subtitles(
        cues=[
            Cue(index=1, start_ms=0, end_ms=5000, lines=["x" * 80]),
            Cue(index=2, start_ms=5000, end_ms=9000, lines=["fine"]),
        ]
    )
    model = _scripted(_edit_block("shorter line"), "done")
    result = Corrector(model=model).correct(subs)
    assert result.structurally_valid is True
    assert structural_violations(result.subtitles) == []


# --- (5) no-LLM mode -------------------------------------------------------


def test_no_llm_mode_applies_safe_fixes_only(broken_srt):
    subs = loads(broken_srt)
    gl = parse_glossary("kuberntes => Kubernetes\nKubernetes\nGitHub\n")
    result = Corrector(model=None, glossary=gl).correct(subs)
    assert result.used_model is False
    # Glossary terms corrected deterministically.
    assert "Kubernetes" in result.subtitles.cues[0].text
    assert "GitHub" in result.subtitles.cues[0].text
    # Cue count + timing preserved.
    assert len(result.subtitles) == len(subs)
    for before, after in zip(subs.cues, result.subtitles.cues, strict=True):
        assert (after.start_ms, after.end_ms, after.index) == (
            before.start_ms,
            before.end_ms,
            before.index,
        )


def test_no_llm_mode_never_raises_and_no_glossary(valid_srt):
    subs = loads(valid_srt)
    result = Corrector(model=None).correct(subs)
    assert result.used_model is False
    assert result.structurally_valid is True


# --- (6) telemetry ---------------------------------------------------------


def test_telemetry_recorded_when_model_present(broken_srt):
    subs = loads(broken_srt)
    telemetry = Telemetry()
    model = _scripted(
        _edit_block("Kubernetes and GitHub."),
        "done",
        _edit_block("Oops."),
        "done",
        model_name="claude-opus-4-8",
    )
    result = Corrector(model=model, telemetry=telemetry).correct(subs)
    tel = result.telemetry.as_dict()
    assert tel["calls"] > 0
    # estimate_cost computed a non-zero cost for the priced model.
    assert tel["total_cost_usd"] > 0


def test_telemetry_uses_estimate_cost_zero_for_unknown_model(broken_srt):
    subs = loads(broken_srt)
    model = _scripted(_edit_block("a"), "done", _edit_block("b"), "done")
    result = Corrector(model=model).correct(subs)
    # Unknown model name => $0.00 but calls still recorded.
    tel = result.telemetry.as_dict()
    assert tel["calls"] > 0
    assert tel["total_cost_usd"] == 0.0


# --- (7) only flagged cues sent to the model -------------------------------


def test_only_flagged_cues_edited():
    # Cue 2 is clean; only cue 1 is flagged (over CPL).
    subs = Subtitles(
        cues=[
            Cue(index=1, start_ms=0, end_ms=5000, lines=["x" * 80]),
            Cue(index=2, start_ms=5000, end_ms=9000, lines=["clean text"]),
        ]
    )
    model = _scripted(_edit_block("fixed short line"), "done")
    result = Corrector(model=model).correct(subs)
    # Cue 2 untouched verbatim.
    assert result.subtitles.cues[1].lines == ["clean text"]
    # An edit only recorded for the flagged cue.
    assert {e.cue_index for e in result.edits} == {1}


def test_unflagged_doc_makes_no_model_calls(valid_srt):
    subs = loads(valid_srt)
    model = _scripted(_edit_block("never used"), "done")
    result = Corrector(model=model).correct(subs)
    # No cue flagged => no edits, model script never consumed.
    assert result.edits == []
    assert model.calls == []


# --- correct_cue_text + CueEdit dataclass ----------------------------------


def test_correct_cue_text_returns_model_lines():
    model = _scripted(_edit_block("line one\\nline two"), "done")
    corrector = Corrector(model=model)
    out = corrector.correct_cue_text(1, ["old"])
    assert out == ["line one", "line two"]


def test_correct_cue_text_falls_back_to_original_when_no_edit():
    model = _scripted("I am not calling any tool.", "still no tool.")
    corrector = Corrector(model=model, max_attempts=1)
    out = corrector.correct_cue_text(1, ["keep me"])
    assert out == ["keep me"]


def test_correct_cue_text_requires_model():
    corrector = Corrector(model=None)
    try:
        corrector.correct_cue_text(1, ["x"])
    except RuntimeError as exc:
        assert "model" in str(exc).lower()
    else:
        raise AssertionError("expected RuntimeError when no model configured")


def test_cue_edit_changed_and_as_dict():
    applied = CueEdit(cue_index=1, before=["a"], after=["b"], applied=True)
    assert applied.changed is True
    no_change = CueEdit(cue_index=1, before=["a"], after=["a"], applied=True)
    assert no_change.changed is False
    rejected = CueEdit(cue_index=1, before=["a"], after=["b"], applied=False)
    assert rejected.changed is False
    d = applied.as_dict()
    assert d["cue_index"] == 1
    assert d["before"] == ["a"]
    assert d["after"] == ["b"]
    assert json.dumps(d)


def test_correction_result_as_dict_serializable(broken_srt):
    subs = loads(broken_srt)
    gl = parse_glossary("kuberntes => Kubernetes\n")
    result = Corrector(model=None, glossary=gl).correct(subs)
    d = result.as_dict()
    assert json.dumps(d)
    assert d["used_model"] is False
    assert "structurally_valid" in d
    assert "edits" in d


def test_correct_accepts_precomputed_report(broken_srt):
    from subtitle_medic.rules import check

    subs = loads(broken_srt)
    report = check(subs)
    result = Corrector(model=None, glossary=parse_glossary("kuberntes => Kubernetes\n")).correct(
        subs, report=report
    )
    assert result.pre_report is report


def test_safe_normalize_token_edges():
    gl = parse_glossary("github => GitHub\nKubernetes\n")
    corrector = Corrector(model=None, glossary=gl)
    # Empty token (double space) is preserved; punctuation around a term is kept.
    assert corrector._apply_glossary_token("") == ""
    assert corrector._apply_glossary_token("(github)") == "(GitHub)"
    assert corrector._apply_glossary_token("github.") == "GitHub."
    # All-punctuation token has no alnum core -> returned verbatim.
    assert corrector._apply_glossary_token("---") == "---"
    assert corrector._apply_glossary_token("...") == "..."
    # Ungoverned token is untouched.
    assert corrector._apply_glossary_token("python") == "python"


def test_safe_normalize_collapses_whitespace():
    corrector = Corrector(model=None, glossary=parse_glossary("Kubernetes\n"))
    out = corrector._safe_normalize(["we   use    Kubernetes  "])
    assert out == ["we use Kubernetes"]


def test_revalidation_gate_rejects_when_signature_grows(monkeypatch):
    # Force the post-edit structural check to report a *new* signature so the
    # defensive rejection branch is exercised; the original cue is kept.
    import subtitle_medic.corrector as corrector_mod
    from subtitle_medic.rules import RuleId, Severity, Violation

    subs = Subtitles(
        cues=[Cue(index=1, start_ms=0, end_ms=5000, lines=["x" * 80])],
    )
    calls = {"n": 0}
    real = corrector_mod.structural_violations

    def fake(document):
        calls["n"] += 1
        # First call: baseline (clean). Later calls (the trial): inject a breakage.
        if calls["n"] == 1:
            return real(document)
        return [Violation(RuleId.TIMING_ORDER, Severity.ERROR, 1, "injected")]

    monkeypatch.setattr(corrector_mod, "structural_violations", fake)
    model = _scripted(_edit_block("short fixed line"), "done")
    result = Corrector(model=model).correct(subs)
    rejected = [e for e in result.edits if not e.applied]
    assert rejected
    assert "rejected" in rejected[0].reason
    # Original text kept.
    assert result.subtitles.cues[0].lines == ["x" * 80]


def test_vtt_correction_preserves_settings_and_header(valid_vtt):
    # Build a flagged VTT cue (over CPL) and confirm header/settings survive.
    subs = loads(valid_vtt)
    over = subs.cues[1].with_lines(["w" * 80])
    cues = list(subs.cues)
    cues[1] = over
    flagged_subs = subs.with_cues(cues)
    model = _scripted(_edit_block("short corrected line"), "done")
    result = Corrector(model=model).correct(flagged_subs)
    assert result.subtitles.format == CaptionFormat.VTT
    assert result.subtitles.header == subs.header
    # The edited cue keeps its settings.
    assert result.subtitles.cues[1].settings == "align:start position:10%"
