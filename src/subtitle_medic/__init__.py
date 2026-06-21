"""subtitle-medic — caption QA + correction agent built on replykit.

Parses SRT and WebVTT into a shared cue model, runs deterministic rule checks
(timing/index invariants plus configurable CPS/CPL/line/duration thresholds), and
— for flagged cues only — drives a replykit :class:`~replykit.Agent` to propose
cue-scoped text corrections that never alter timing or cue count, re-validating
every structural invariant before emitting a file.

This is the public library surface the CLI and downstream automation build on.
Importing ``subtitle_medic`` does not import any LLM SDK: the correction path
talks to any object satisfying replykit's ``Model`` protocol, and a missing
provider degrades to a graceful rule-checks-only ("no-LLM") mode.
"""

from __future__ import annotations

from .corrector import CorrectionResult, Corrector, CueEdit
from .glossary import EMPTY_GLOSSARY, Glossary, load_glossary, parse_glossary
from .model import CaptionFormat, Cue, Subtitles
from .parser import (
    ParseError,
    detect_format,
    dump,
    dumps,
    format_timecode,
    load,
    loads,
    parse_timecode,
)
from .report import (
    check_report_json,
    check_report_markdown,
    correction_report_json,
    correction_report_markdown,
)
from .rules import (
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

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # model
    "CaptionFormat",
    "Cue",
    "Subtitles",
    # parser
    "ParseError",
    "detect_format",
    "parse_timecode",
    "format_timecode",
    "loads",
    "dumps",
    "load",
    "dump",
    # rules
    "RuleId",
    "Severity",
    "CheckConfig",
    "Violation",
    "CheckReport",
    "STRUCTURAL_RULES",
    "check",
    "structural_violations",
    "readability_violations",
    # glossary
    "Glossary",
    "EMPTY_GLOSSARY",
    "parse_glossary",
    "load_glossary",
    # corrector
    "Corrector",
    "CorrectionResult",
    "CueEdit",
    # report
    "check_report_markdown",
    "check_report_json",
    "correction_report_markdown",
    "correction_report_json",
]
