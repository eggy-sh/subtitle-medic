"""Round-trip and structural tests for the SRT/WebVTT parser."""

from __future__ import annotations

import pytest

from subtitle_medic.model import CaptionFormat
from subtitle_medic.parser import (
    ParseError,
    detect_format,
    dump,
    dumps,
    format_timecode,
    load,
    loads,
    parse_timecode,
)

# --- detect_format ---------------------------------------------------------


def test_detect_format_srt(valid_srt):
    assert detect_format(valid_srt) == CaptionFormat.SRT


def test_detect_format_vtt(valid_vtt):
    assert detect_format(valid_vtt) == CaptionFormat.VTT


def test_detect_format_bom_prefixed_vtt(valid_vtt):
    bom_vtt = "﻿" + valid_vtt
    assert detect_format(bom_vtt) == CaptionFormat.VTT


def test_detect_format_leading_whitespace_vtt(valid_vtt):
    assert detect_format("\n\n" + valid_vtt) == CaptionFormat.VTT


# --- timecode --------------------------------------------------------------


def test_parse_timecode_comma_separator():
    assert parse_timecode("00:00:01,500") == 1500


def test_parse_timecode_dot_separator():
    assert parse_timecode("00:00:01.500") == 1500


def test_parse_timecode_missing_hours():
    assert parse_timecode("01:30.250") == 90250


def test_parse_timecode_with_hours():
    assert parse_timecode("01:01:01,001") == 3661001


@pytest.mark.parametrize("bad", ["", "garbage", "1:2:3:4", "aa:bb:cc,ddd", "00:00:01"])
def test_parse_timecode_raises_on_garbage(bad):
    with pytest.raises(ParseError):
        parse_timecode(bad)


def test_format_timecode_srt_uses_comma():
    assert format_timecode(3661001, CaptionFormat.SRT) == "01:01:01,001"


def test_format_timecode_vtt_uses_dot():
    assert format_timecode(3661001, CaptionFormat.VTT) == "01:01:01.001"


def test_format_timecode_clamps_negative_to_zero():
    assert format_timecode(-5, CaptionFormat.SRT) == "00:00:00,000"


@pytest.mark.parametrize(
    "canonical",
    ["00:00:01,000", "00:00:03,500", "01:01:01,999", "00:59:59,001"],
)
def test_format_of_parse_is_canonical_srt(canonical):
    assert format_timecode(parse_timecode(canonical), CaptionFormat.SRT) == canonical


@pytest.mark.parametrize(
    "canonical",
    ["00:00:01.000", "00:00:03.500", "01:01:01.999"],
)
def test_format_of_parse_is_canonical_vtt(canonical):
    assert format_timecode(parse_timecode(canonical), CaptionFormat.VTT) == canonical


# --- SRT round-trip --------------------------------------------------------


def test_srt_loads_basic_fields(valid_srt):
    subs = loads(valid_srt)
    assert subs.format == CaptionFormat.SRT
    assert len(subs) == 3
    assert [c.index for c in subs.cues] == [1, 2, 3]
    assert subs.cues[0].start_ms == 1000
    assert subs.cues[0].end_ms == 3000
    assert subs.cues[1].lines == ["This is a second cue", "spanning two lines."]


def test_srt_round_trip_dumps_loads_equal(valid_srt):
    subs = loads(valid_srt)
    again = loads(dumps(subs))
    assert again == subs


def test_srt_dumps_loads_is_byte_stable(valid_srt):
    subs = loads(valid_srt)
    first = dumps(subs)
    second = dumps(loads(first))
    assert first == second


def test_srt_dumps_equals_normalized_input(valid_srt):
    # The provided VALID_SRT is already canonical (LF, single blank separators),
    # so dumps(loads(text)) reproduces it exactly.
    subs = loads(valid_srt)
    assert dumps(subs) == valid_srt


# --- VTT round-trip --------------------------------------------------------


def test_vtt_loads_header_and_settings(valid_vtt):
    subs = loads(valid_vtt)
    assert subs.format == CaptionFormat.VTT
    assert len(subs) == 3
    # Header preserves Kind and the NOTE block.
    joined = "\n".join(subs.header)
    assert "Kind: captions" in joined
    assert "NOTE This is a sample file." in joined
    # Per-cue settings preserved verbatim.
    assert subs.cues[1].settings == "align:start position:10%"


def test_vtt_round_trip_dumps_loads_equal(valid_vtt):
    subs = loads(valid_vtt)
    again = loads(dumps(subs))
    assert again == subs


def test_vtt_round_trip_preserves_header_and_settings(valid_vtt):
    subs = loads(valid_vtt)
    out = dumps(subs)
    assert "WEBVTT" in out
    assert "Kind: captions" in out
    assert "NOTE This is a sample file." in out
    assert "align:start position:10%" in out
    reparsed = loads(out)
    assert reparsed.header == subs.header
    assert reparsed.cues[1].settings == subs.cues[1].settings


# --- tolerance / strictness ------------------------------------------------


def test_crlf_and_trailing_whitespace_normalizes(valid_srt):
    messy = valid_srt.replace("\n", "\r\n")
    messy = messy.replace("Hello world.", "Hello world.   ")
    subs = loads(messy)
    assert subs.cues[0].lines == ["Hello world."]
    assert len(subs) == 3


def test_blank_line_padding_tolerated(valid_srt):
    padded = "\n\n\n" + valid_srt + "\n\n\n"
    subs = loads(padded)
    assert len(subs) == 3


def test_bom_tolerated_on_load(valid_srt):
    subs = loads("﻿" + valid_srt)
    assert len(subs) == 3


def test_malformed_cue_missing_arrow_raises_naming_cue():
    bad = "1\n00:00:01,000 00:00:03,000\nNo arrow here.\n"
    with pytest.raises(ParseError) as excinfo:
        loads(bad)
    assert "1" in str(excinfo.value)


def test_malformed_index_raises():
    bad = "notanumber\n00:00:01,000 --> 00:00:03,000\nText.\n"
    with pytest.raises(ParseError):
        loads(bad)


def test_cue_with_id_but_no_timing_line_raises():
    bad = "1\n"  # an index line and nothing else (no blank, no timing)
    with pytest.raises(ParseError) as excinfo:
        loads(bad)
    assert "1" in str(excinfo.value)


def test_no_trailing_newline_still_parses():
    # The final block has no trailing blank line; the parser still flushes it.
    text = "1\n00:00:01,000 --> 00:00:03,000\nNo trailing newline."
    subs = loads(text)
    assert len(subs) == 1
    assert subs.cues[0].lines == ["No trailing newline."]


def test_vtt_standalone_note_block_kept_in_header(valid_vtt):
    # The NOTE in VALID_VTT is its own block after the header; it must land in
    # the header, not become a cue.
    subs = loads(valid_vtt)
    assert any("NOTE" in line for line in subs.header)
    assert len(subs) == 3


def test_vtt_cue_without_identifier_line():
    # A VTT cue whose first line is the timing line (no preceding id/index).
    text = "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nNo id line.\n"
    subs = loads(text)
    assert len(subs) == 1
    assert subs.cues[0].index == 1
    assert subs.cues[0].lines == ["No id line."]


def test_vtt_multiple_note_blocks_accumulate_in_header():
    text = "WEBVTT\n\nNOTE first note\n\nNOTE second note\n\n00:00:01.000 --> 00:00:03.000\nText.\n"
    subs = loads(text)
    joined = "\n".join(subs.header)
    assert "first note" in joined
    assert "second note" in joined
    assert len(subs) == 1


def test_vtt_cue_with_identifier_line():
    text = "WEBVTT\n\nintro\n00:00:01.000 --> 00:00:03.000\nHi.\n"
    subs = loads(text)
    assert len(subs) == 1
    # The identifier line is ignored; ordinal index synthesized.
    assert subs.cues[0].index == 1
    assert subs.cues[0].lines == ["Hi."]


def test_vtt_without_signature_raises():
    bad = "1\n00:00:01.000 --> 00:00:03.000\nText.\n"
    with pytest.raises(ParseError):
        loads(bad, fmt=CaptionFormat.VTT)


# --- file I/O --------------------------------------------------------------


def test_load_reads_file(tmp_srt_file):
    subs = load(str(tmp_srt_file))
    assert len(subs) == 3


def test_dump_writes_reparseable_file(tmp_path, valid_srt):
    subs = loads(valid_srt)
    out = tmp_path / "out.srt"
    dump(subs, str(out))
    reloaded = load(str(out))
    assert reloaded == subs
    # LF newlines only.
    assert "\r" not in out.read_text(encoding="utf-8")


def test_dump_vtt_round_trips(tmp_path, valid_vtt):
    subs = loads(valid_vtt)
    out = tmp_path / "out.vtt"
    dump(subs, str(out))
    reloaded = load(str(out))
    assert reloaded == subs


# --- studio-correctness interop --------------------------------------------


def test_emitted_files_are_safe_to_hand_back(valid_srt, valid_vtt):
    from subtitle_medic.rules import structural_violations

    for text in (valid_srt, valid_vtt):
        subs = loads(text)
        emitted = dumps(subs)
        # (a) re-parses without error
        reparsed = loads(emitted)
        # (b) preserves cue count + per-cue timing
        assert len(reparsed) == len(subs)
        for a, b in zip(reparsed.cues, subs.cues, strict=True):
            assert (a.start_ms, a.end_ms, a.index) == (b.start_ms, b.end_ms, b.index)
        # (c) passes structural checks
        assert structural_violations(reparsed) == []
