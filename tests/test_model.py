"""Tests for the format-agnostic cue domain model."""

from __future__ import annotations

import json

from subtitle_medic.model import CaptionFormat, Cue, Subtitles


def test_duration_ms_is_end_minus_start():
    cue = Cue(index=1, start_ms=1000, end_ms=3500, lines=["hi"])
    assert cue.duration_ms == 2500


def test_duration_ms_can_be_non_positive():
    cue = Cue(index=1, start_ms=3000, end_ms=3000, lines=["hi"])
    assert cue.duration_ms == 0
    cue2 = Cue(index=1, start_ms=3000, end_ms=2000, lines=["hi"])
    assert cue2.duration_ms == -1000


def test_text_joins_lines_with_newline():
    cue = Cue(index=1, start_ms=0, end_ms=1000, lines=["line one", "line two"])
    assert cue.text == "line one\nline two"


def test_text_single_line():
    cue = Cue(index=1, start_ms=0, end_ms=1000, lines=["only"])
    assert cue.text == "only"


def test_char_count_excludes_newlines_counts_spaces_and_unicode():
    cue = Cue(index=1, start_ms=0, end_ms=1000, lines=["ab c", "déf"])
    # "ab c" = 4 (incl space), "déf" = 3 (unicode é counted once) -> 7, no newline.
    assert cue.char_count == 7


def test_cps_is_char_count_over_seconds():
    # 20 chars over 2 seconds => 10.0 cps.
    cue = Cue(index=1, start_ms=0, end_ms=2000, lines=["x" * 20])
    assert cue.cps() == 10.0


def test_cps_zero_when_duration_non_positive_no_zero_division():
    cue = Cue(index=1, start_ms=1000, end_ms=1000, lines=["hello"])
    assert cue.cps() == 0.0
    cue_neg = Cue(index=1, start_ms=2000, end_ms=1000, lines=["hello"])
    assert cue_neg.cps() == 0.0


def test_with_lines_replaces_lines_keeps_identity_and_timing():
    original = Cue(
        index=7,
        start_ms=1000,
        end_ms=3000,
        lines=["old"],
        settings="align:start",
    )
    edited = original.with_lines(["new line a", "new line b"])
    assert edited.lines == ["new line a", "new line b"]
    # Field-by-field: everything except lines is identical.
    assert edited.index == original.index
    assert edited.start_ms == original.start_ms
    assert edited.end_ms == original.end_ms
    assert edited.settings == original.settings


def test_with_lines_returns_a_copy_not_mutating_original():
    original = Cue(index=1, start_ms=0, end_ms=1000, lines=["a"])
    edited = original.with_lines(["b"])
    assert original.lines == ["a"]
    assert edited is not original


def test_subtitles_len():
    subs = Subtitles(
        cues=[
            Cue(index=1, start_ms=0, end_ms=1000, lines=["a"]),
            Cue(index=2, start_ms=1000, end_ms=2000, lines=["b"]),
        ]
    )
    assert len(subs) == 2


def test_subtitles_with_cues_preserves_format_and_header():
    subs = Subtitles(
        cues=[Cue(index=1, start_ms=0, end_ms=1000, lines=["a"])],
        format=CaptionFormat.VTT,
        header=["Kind: captions", "NOTE hi"],
    )
    new_cue = Cue(index=1, start_ms=0, end_ms=1000, lines=["b"])
    out = subs.with_cues([new_cue])
    assert out.format == CaptionFormat.VTT
    assert out.header == ["Kind: captions", "NOTE hi"]
    assert out.cues[0].lines == ["b"]
    # Original is untouched.
    assert subs.cues[0].lines == ["a"]


def test_caption_format_str_enum_values():
    assert CaptionFormat.SRT == "srt"
    assert CaptionFormat.VTT == "vtt"
    assert str(CaptionFormat.SRT) == "srt"


def test_as_dict_round_trips_through_json():
    subs = Subtitles(
        cues=[
            Cue(index=1, start_ms=0, end_ms=1000, lines=["a", "b"], settings="x"),
            Cue(index=2, start_ms=1000, end_ms=2000, lines=["c"]),
        ],
        format=CaptionFormat.VTT,
        header=["Kind: captions"],
    )
    d = subs.as_dict()
    encoded = json.dumps(d)
    decoded = json.loads(encoded)
    assert decoded["format"] == "vtt"
    assert decoded["header"] == ["Kind: captions"]
    assert decoded["cues"][0]["start_ms"] == 0
    assert decoded["cues"][0]["lines"] == ["a", "b"]
    assert decoded["cues"][0]["settings"] == "x"
    assert decoded["cues"][1]["index"] == 2
