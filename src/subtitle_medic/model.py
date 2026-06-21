"""The caption domain model: a format-agnostic cue list.

A :class:`Cue` is one subtitle event — index, start/end timecodes (milliseconds
since zero), and one or more text lines. A :class:`Subtitles` document is an
ordered list of cues plus the source :class:`CaptionFormat`, so a parsed file can
be re-serialized back to the *same* format and round-trip byte-for-byte on
well-formed input.

This module is pure data and small deterministic helpers — no I/O, no LLM, no
third-party deps — so it imports cleanly and the rule engine, parser, corrector,
and reporter all share one cue representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class CaptionFormat(StrEnum):
    """The subtitle container formats subtitle-medic can parse and emit."""

    SRT = "srt"
    VTT = "vtt"


@dataclass(frozen=True)
class Cue:
    """One subtitle event.

    ``index`` is the 1-based display order (SRT's numeric counter; for VTT it is
    the synthesized ordinal). ``start_ms`` / ``end_ms`` are integer milliseconds
    from zero. ``lines`` is the wrapped display text split on hard line breaks
    (never including the trailing blank separator). ``settings`` preserves any
    WebVTT cue-setting suffix (e.g. ``"align:start position:10%"``) verbatim so a
    VTT round-trips; it is empty for SRT.
    """

    index: int
    start_ms: int
    end_ms: int
    lines: list[str]
    settings: str = ""

    @property
    def duration_ms(self) -> int:
        """End minus start, in milliseconds (may be <= 0 on malformed timing)."""
        return self.end_ms - self.start_ms

    @property
    def text(self) -> str:
        """The cue's display text with hard line breaks joined by ``\\n``."""
        return "\n".join(self.lines)

    @property
    def char_count(self) -> int:
        """Total visible characters across all lines, excluding line breaks."""
        return sum(len(line) for line in self.lines)

    def cps(self) -> float:
        """Characters per second over the cue duration.

        Returns ``0.0`` for a non-positive duration so callers never divide by
        zero; the timing rule check flags the bad duration separately.
        """
        if self.duration_ms <= 0:
            return 0.0
        return self.char_count / (self.duration_ms / 1000.0)

    def with_lines(self, lines: list[str]) -> Cue:
        """Return a copy with replaced text lines; index and timing are unchanged.

        The corrector uses this to apply a text-only edit while guaranteeing the
        cue's identity and timing are structurally preserved.
        """
        return replace(self, lines=list(lines))


@dataclass
class Subtitles:
    """An ordered caption document plus its source format."""

    cues: list[Cue] = field(default_factory=list)
    format: CaptionFormat = CaptionFormat.SRT
    # Verbatim WebVTT header lines after the ``WEBVTT`` signature and before the
    # first cue (e.g. ``NOTE`` blocks, ``STYLE`` blocks, ``Kind: captions``).
    # Empty for SRT. Preserved so a VTT round-trips.
    header: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.cues)

    def with_cues(self, cues: list[Cue]) -> Subtitles:
        """Return a copy carrying new cues but the same format and header."""
        return replace(self, cues=list(cues))

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the document (used by ``--json``)."""
        return {
            "format": str(self.format),
            "header": list(self.header),
            "cues": [
                {
                    "index": cue.index,
                    "start_ms": cue.start_ms,
                    "end_ms": cue.end_ms,
                    "lines": list(cue.lines),
                    "settings": cue.settings,
                }
                for cue in self.cues
            ],
        }
