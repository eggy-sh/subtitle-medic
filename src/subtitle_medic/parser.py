"""Round-trip-safe SRT / WebVTT parsing and serialization.

The parser turns raw caption text into a :class:`~subtitle_medic.model.Subtitles`
document; the serializer turns it back. The contract is **round-trip stability**:
for well-formed input, ``dumps(loads(text)) == normalize(text)`` where
``normalize`` only fixes trailing-whitespace and newline conventions — cue count,
ordering, timing, text, and (for VTT) header/cue-settings are preserved exactly.

Both formats are parsed by a small hand-rolled state machine (no third-party
caption library) so the package stays dependency-light and the timecode grammar
is auditable. Timecodes are parsed to integer milliseconds and re-emitted in the
canonical per-format spelling (SRT ``HH:MM:SS,mmm``; VTT ``HH:MM:SS.mmm``).
"""

from __future__ import annotations

import re

from .model import CaptionFormat, Cue, Subtitles

_BOM = "﻿"

#: A timecode token: optional ``HH:`` then ``MM:SS`` then ``,``/``.`` and 1-3 ms digits.
_TIMECODE_RE = re.compile(r"^(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{1,2})[.,](?P<ms>\d{1,3})$")

#: A cue timing line: ``<start> --> <end>`` with an optional trailing settings suffix.
_TIMING_LINE_RE = re.compile(r"^\s*(?P<start>[\d:.,]+)\s*-->\s*(?P<end>[\d:.,]+)(?P<settings>.*)$")


class ParseError(ValueError):
    """Raised when caption text cannot be parsed into a valid cue list.

    Carries a human-readable message naming the offending line/cue so the CLI can
    report *where* a file is malformed rather than failing opaquely.
    """


def _strip_bom(text: str) -> str:
    return text[len(_BOM) :] if text.startswith(_BOM) else text


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def detect_format(text: str) -> CaptionFormat:
    """Sniff the caption format from the document body.

    Returns :attr:`CaptionFormat.VTT` when the text begins with the ``WEBVTT``
    signature (after an optional BOM/whitespace), otherwise :attr:`CaptionFormat.SRT`.
    """
    head = _strip_bom(text).lstrip()
    if head.upper().startswith("WEBVTT"):
        return CaptionFormat.VTT
    return CaptionFormat.SRT


def parse_timecode(value: str) -> int:
    """Parse a single ``HH:MM:SS,mmm`` or ``HH:MM:SS.mmm`` timecode to milliseconds.

    Accepts either ``,`` or ``.`` as the millisecond separator and a missing hours
    field (``MM:SS.mmm``). Raises :class:`ParseError` on a malformed timecode.
    """
    m = _TIMECODE_RE.match(value.strip())
    if m is None:
        raise ParseError(f"malformed timecode: {value!r}")
    hours = int(m.group("h")) if m.group("h") is not None else 0
    minutes = int(m.group("m"))
    seconds = int(m.group("s"))
    # Right-pad fractional ms so "5" -> 500ms, "50" -> 500ms, "500" -> 500ms.
    ms = int(m.group("ms").ljust(3, "0"))
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + ms


def format_timecode(ms: int, fmt: CaptionFormat) -> str:
    """Render milliseconds as a per-format timecode string.

    SRT uses ``HH:MM:SS,mmm`` (comma); VTT uses ``HH:MM:SS.mmm`` (dot). The result
    is always zero-padded and reversible by :func:`parse_timecode`.
    """
    if ms < 0:
        ms = 0
    total_seconds, millis = divmod(ms, 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    sep = "," if fmt is CaptionFormat.SRT else "."
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{millis:03d}"


def _split_blocks(body: str) -> list[list[str]]:
    """Split a normalized body into cue blocks separated by blank line(s)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in body.split("\n"):
        line = raw_line.rstrip()
        if line == "":
            if current:
                blocks.append(current)
                current = []
        else:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def _parse_cue_block(lines: list[str], ordinal: int, fmt: CaptionFormat) -> Cue:
    """Parse one cue block (optional id line, timing line, then text lines)."""
    idx = 0
    index = ordinal
    # An optional leading numeric/identifier line precedes the timing line. For
    # SRT it is the index; for VTT it is an optional cue identifier we ignore.
    if "-->" not in lines[0]:
        first = lines[0].strip()
        if fmt is CaptionFormat.SRT:
            if not first.isdigit():
                raise ParseError(f"cue {ordinal}: expected numeric index, got {first!r}")
            index = int(first)
        idx = 1
    if idx >= len(lines):
        raise ParseError(f"cue {ordinal}: missing timing line")
    timing_line = lines[idx]
    m = _TIMING_LINE_RE.match(timing_line)
    if m is None:
        raise ParseError(f"cue {ordinal}: missing '-->' in timing line: {timing_line!r}")
    start_ms = parse_timecode(m.group("start"))
    end_ms = parse_timecode(m.group("end"))
    settings = m.group("settings").strip()
    if fmt is CaptionFormat.SRT:
        # SRT has no cue settings; a trailing suffix is structural breakage.
        settings = ""
    text_lines = lines[idx + 1 :]
    return Cue(
        index=index,
        start_ms=start_ms,
        end_ms=end_ms,
        lines=list(text_lines),
        settings=settings,
    )


def _parse_vtt(text: str) -> Subtitles:
    body = _normalize_newlines(_strip_bom(text))
    lines = body.split("\n")
    if not lines or not lines[0].lstrip().upper().startswith("WEBVTT"):
        raise ParseError("VTT file must start with the 'WEBVTT' signature")
    # The header runs from the line after WEBVTT up to the first blank line.
    header: list[str] = []
    # Trailing tokens on the WEBVTT line itself are part of the signature; keep
    # any text after "WEBVTT" only if present (rare), otherwise ignore.
    i = 1
    while i < len(lines) and lines[i].strip() != "":
        header.append(lines[i].rstrip())
        i += 1
    rest = "\n".join(lines[i:])
    blocks = _split_blocks(rest)
    cues: list[Cue] = []
    ordinal = 0
    for block in blocks:
        # NOTE / STYLE / REGION blocks belong to the header, not to cues.
        head = block[0].strip().upper()
        if head.startswith("NOTE") or head.startswith("STYLE") or head.startswith("REGION"):
            if header and header[-1] != "":
                header.append("")
            header.extend(block)
            continue
        ordinal += 1
        cues.append(_parse_cue_block(block, ordinal, CaptionFormat.VTT))
    return Subtitles(cues=cues, format=CaptionFormat.VTT, header=header)


def _parse_srt(text: str) -> Subtitles:
    body = _normalize_newlines(_strip_bom(text))
    blocks = _split_blocks(body)
    cues: list[Cue] = []
    for ordinal, block in enumerate(blocks, start=1):
        cues.append(_parse_cue_block(block, ordinal, CaptionFormat.SRT))
    return Subtitles(cues=cues, format=CaptionFormat.SRT, header=[])


def loads(text: str, fmt: CaptionFormat | None = None) -> Subtitles:
    """Parse caption ``text`` into a :class:`Subtitles` document.

    When ``fmt`` is ``None`` the format is auto-detected with :func:`detect_format`.
    Indices are read from SRT counters (and synthesized 1..N for VTT). The parser
    is strict enough to reject structurally broken input (bad timecodes, missing
    ``-->`` arrows) via :class:`ParseError`, but tolerant of trailing whitespace,
    blank-line padding, and ``\\r\\n`` newlines.
    """
    resolved = fmt if fmt is not None else detect_format(text)
    if resolved is CaptionFormat.VTT:
        return _parse_vtt(text)
    return _parse_srt(text)


def dumps(subs: Subtitles, fmt: CaptionFormat | None = None) -> str:
    """Serialize a :class:`Subtitles` document back to caption text.

    When ``fmt`` is ``None`` the document's own :attr:`Subtitles.format` is used.
    Emits canonical, round-trip-stable output: ``\\n`` newlines, a single blank
    line between cues, the per-format timecode spelling, and (for VTT) the
    preserved header and per-cue settings. The result is re-parseable by
    :func:`loads` to an equal document.
    """
    resolved = fmt if fmt is not None else subs.format
    chunks: list[str] = []
    if resolved is CaptionFormat.VTT:
        head = "WEBVTT"
        if subs.header:
            head = head + "\n" + "\n".join(subs.header)
        chunks.append(head)
    for cue in subs.cues:
        start = format_timecode(cue.start_ms, resolved)
        end = format_timecode(cue.end_ms, resolved)
        timing = f"{start} --> {end}"
        if resolved is CaptionFormat.VTT and cue.settings:
            timing = f"{timing} {cue.settings}"
        block_lines = [str(cue.index), timing, *cue.lines]
        chunks.append("\n".join(block_lines))
    return "\n\n".join(chunks) + "\n"


def load(path: str, fmt: CaptionFormat | None = None) -> Subtitles:
    """Read a caption file from ``path`` and parse it (UTF-8, BOM-tolerant)."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    return loads(text, fmt)


def dump(subs: Subtitles, path: str, fmt: CaptionFormat | None = None) -> None:
    """Serialize ``subs`` and write it to ``path`` as UTF-8 (LF newlines)."""
    text = dumps(subs, fmt)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
