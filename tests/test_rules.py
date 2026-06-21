"""Tests for the deterministic rule engine."""

from __future__ import annotations

import json

from subtitle_medic.model import CaptionFormat, Cue, Subtitles
from subtitle_medic.rules import (
    STRUCTURAL_RULES,
    CheckConfig,
    CheckReport,
    RuleId,
    Severity,
    Violation,
    check,
    readability_violations,
    structural_violations,
)


def _subs(*cues: Cue, fmt: CaptionFormat = CaptionFormat.SRT) -> Subtitles:
    return Subtitles(cues=list(cues), format=fmt)


def _cue(index, start, end, lines=None):
    return Cue(index=index, start_ms=start, end_ms=end, lines=lines or ["x"])


# --- clean -----------------------------------------------------------------


def test_clean_srt_passes(valid_srt):
    from subtitle_medic.parser import loads

    report = check(loads(valid_srt))
    assert report.ok is True
    assert report.violations == []
    assert report.has_errors is False


# --- structural rules ------------------------------------------------------


def test_index_continuity_fires_on_gap():
    subs = _subs(_cue(1, 0, 1000), _cue(3, 2000, 3000))
    rules = {v.rule for v in structural_violations(subs)}
    assert RuleId.INDEX_CONTINUITY in rules


def test_timing_order_fires_when_not_strictly_increasing():
    subs = _subs(_cue(1, 2000, 3000), _cue(2, 1000, 4000))
    rules = {v.rule for v in structural_violations(subs)}
    assert RuleId.TIMING_ORDER in rules


def test_timing_overlap_fires_when_start_before_prev_end():
    subs = _subs(_cue(1, 0, 3000), _cue(2, 2000, 5000))
    rules = {v.rule for v in structural_violations(subs)}
    assert RuleId.TIMING_OVERLAP in rules


def test_negative_duration_fires_when_end_le_start():
    subs = _subs(_cue(1, 3000, 3000))
    rules = {v.rule for v in structural_violations(subs)}
    assert RuleId.NEGATIVE_DURATION in rules
    subs2 = _subs(_cue(1, 3000, 2000))
    rules2 = {v.rule for v in structural_violations(subs2)}
    assert RuleId.NEGATIVE_DURATION in rules2


def test_structural_violations_are_errors():
    subs = _subs(_cue(1, 0, 3000), _cue(2, 2000, 5000))
    for v in structural_violations(subs):
        assert v.severity is Severity.ERROR


def test_structural_violations_returns_only_structural_rules():
    # A doc with both structural breakage and readability issues.
    long_line = "y" * 100
    subs = _subs(
        _cue(1, 0, 200, [long_line]),  # too short + over CPL/CPS
        _cue(3, 100, 50),  # index gap + overlap + negative duration
    )
    got = {v.rule for v in structural_violations(subs)}
    assert got <= STRUCTURAL_RULES
    assert RuleId.CPL not in got
    assert RuleId.MIN_DURATION not in got


# --- readability boundaries ------------------------------------------------


def test_min_duration_boundary():
    cfg = CheckConfig()
    passes = _subs(_cue(1, 0, 700))  # exactly 700 ms
    fails = _subs(_cue(1, 0, 699))  # 699 ms
    pass_rules = {v.rule for v in readability_violations(passes, cfg)}
    fail_rules = {v.rule for v in readability_violations(fails, cfg)}
    assert RuleId.MIN_DURATION not in pass_rules
    assert RuleId.MIN_DURATION in fail_rules


def test_max_duration_boundary():
    cfg = CheckConfig()
    passes = _subs(_cue(1, 0, 7000))  # exactly 7000 ms
    fails = _subs(_cue(1, 0, 7001))  # 7001 ms
    assert RuleId.MAX_DURATION not in {v.rule for v in readability_violations(passes, cfg)}
    assert RuleId.MAX_DURATION in {v.rule for v in readability_violations(fails, cfg)}


def test_cps_flagged_with_observed_and_limit():
    cfg = CheckConfig(max_cps=17.0)
    # 100 chars over 1 second => 100 cps, way over.
    subs = _subs(_cue(1, 0, 1000, ["z" * 100]))
    cps = [v for v in readability_violations(subs, cfg) if v.rule is RuleId.CPS]
    assert cps
    assert cps[0].observed is not None
    assert cps[0].limit == 17.0


def test_cpl_per_line_over_max():
    cfg = CheckConfig(max_cpl=42)
    subs = _subs(_cue(1, 0, 5000, ["short", "w" * 50]))
    cpl = [v for v in readability_violations(subs, cfg) if v.rule is RuleId.CPL]
    assert cpl
    assert cpl[0].observed == 50
    assert cpl[0].limit == 42


def test_max_lines_on_three_lines():
    cfg = CheckConfig(max_lines=2)
    subs = _subs(_cue(1, 0, 5000, ["a", "b", "c"]))
    ml = [v for v in readability_violations(subs, cfg) if v.rule is RuleId.MAX_LINES]
    assert ml
    assert ml[0].observed == 3
    assert ml[0].limit == 2


def test_readability_severity_is_warning():
    cfg = CheckConfig()
    subs = _subs(_cue(1, 0, 100, ["q" * 80]))
    for v in readability_violations(subs, cfg):
        assert v.severity is Severity.WARNING


# --- config ----------------------------------------------------------------


def test_check_config_as_dict():
    cfg = CheckConfig(max_cps=20.0, max_cpl=40)
    d = cfg.as_dict()
    assert d["max_cps"] == 20.0
    assert d["max_cpl"] == 40
    assert json.dumps(d)


def test_check_config_from_dict_partial_falls_back_per_field():
    cfg = CheckConfig.from_dict({"max_cpl": 30})
    assert cfg.max_cpl == 30
    # Untouched fields keep defaults.
    assert cfg.max_cps == CheckConfig().max_cps
    assert cfg.max_lines == CheckConfig().max_lines
    assert cfg.min_duration_ms == CheckConfig().min_duration_ms


def test_check_config_from_dict_empty():
    assert CheckConfig.from_dict({}) == CheckConfig()


# --- report rollups + ordering --------------------------------------------


def test_flagged_indices_sorted_and_deduped():
    v1 = Violation(RuleId.CPS, Severity.WARNING, 3, "m")
    v2 = Violation(RuleId.CPL, Severity.WARNING, 1, "m")
    v3 = Violation(RuleId.MAX_LINES, Severity.WARNING, 3, "m")
    report = CheckReport(violations=[v1, v2, v3], cue_count=3)
    assert report.flagged_indices() == [1, 3]


def test_for_cue_returns_only_that_cue():
    v1 = Violation(RuleId.CPS, Severity.WARNING, 1, "a")
    v2 = Violation(RuleId.CPL, Severity.WARNING, 2, "b")
    report = CheckReport(violations=[v1, v2], cue_count=2)
    got = report.for_cue(1)
    assert got == [v1]


def test_violation_is_structural_and_as_dict():
    v = Violation(RuleId.TIMING_OVERLAP, Severity.ERROR, 2, "msg", observed=1, limit=2)
    assert v.is_structural is True
    d = v.as_dict()
    assert d["rule"] == "timing_overlap"
    assert d["severity"] == "error"
    assert d["cue_index"] == 2
    assert json.dumps(d)
    warn = Violation(RuleId.CPS, Severity.WARNING, 1, "m")
    assert warn.is_structural is False


def test_check_report_as_dict_serializable():
    subs = _subs(_cue(1, 0, 3000), _cue(2, 2000, 5000))
    report = check(subs)
    d = report.as_dict()
    assert json.dumps(d)
    assert d["summary"]["errors"] >= 1


def test_violation_ordering_structural_first_then_index_then_rule():
    long_line = "y" * 100
    subs = _subs(
        _cue(1, 0, 300, [long_line]),  # readability: min_dur, cps, cpl
        _cue(3, 200, 100),  # structural: index gap, overlap, negative dur
    )
    report = check(subs)
    # All structural violations come before all readability ones.
    families = [v.is_structural for v in report.violations]
    # Once we see a False (readability), no True should follow.
    seen_readability = False
    for is_struct in families:
        if not is_struct:
            seen_readability = True
        elif seen_readability:
            raise AssertionError("structural violation appeared after a readability one")
    # Deterministic: same input yields same order twice.
    assert check(subs).violations == report.violations


def test_check_uses_default_config_when_none():
    subs = _subs(_cue(1, 0, 100, ["q" * 80]))
    report = check(subs, None)
    rules = {v.rule for v in report.violations}
    assert RuleId.MIN_DURATION in rules
