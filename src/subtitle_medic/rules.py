"""Deterministic caption QA rule checks.

Given a :class:`~subtitle_medic.model.Subtitles` document and a :class:`CheckConfig`,
:func:`check` returns an ordered list of :class:`Violation` objects — every rule
breach found, with a stable :class:`RuleId`, the offending cue index, a severity,
a human message, and a machine-readable ``observed`` / ``limit`` payload.

The checks split into two families:

* **Structural invariants** (must always hold for a file to be valid): index
  continuity, monotonic non-overlapping timing, positive duration. These are the
  invariants the corrector must never break — :func:`structural_violations`
  isolates them so the corrector can re-validate after editing.
* **Readability thresholds** (configurable studio limits): CPS, CPL, max lines,
  and min/max duration.

This module is pure and deterministic — no LLM, no I/O. The same input always
yields the same violation list in the same order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .model import Cue, Subtitles


class RuleId(StrEnum):
    """Stable identifiers for each rule check (used in reports and ``--json``)."""

    INDEX_CONTINUITY = "index_continuity"
    TIMING_ORDER = "timing_order"  # start strictly increasing across cues
    TIMING_OVERLAP = "timing_overlap"  # cue starts before previous cue ends
    NEGATIVE_DURATION = "negative_duration"  # end <= start within a cue
    MIN_DURATION = "min_duration"
    MAX_DURATION = "max_duration"
    CPS = "cps"  # characters per second too high
    CPL = "cpl"  # a line exceeds max characters per line
    MAX_LINES = "max_lines"  # too many lines in one cue


class Severity(StrEnum):
    """How serious a violation is. ``ERROR`` marks a broken structural invariant."""

    ERROR = "error"
    WARNING = "warning"


#: Rule ids that represent structural invariants. A corrected document that
#: introduces any of these is rejected — the tool never emits a file that breaks
#: indexing, ordering, or round-trip.
STRUCTURAL_RULES: frozenset[RuleId] = frozenset(
    {
        RuleId.INDEX_CONTINUITY,
        RuleId.TIMING_ORDER,
        RuleId.TIMING_OVERLAP,
        RuleId.NEGATIVE_DURATION,
    }
)


@dataclass(frozen=True)
class CheckConfig:
    """Configurable readability thresholds. Defaults track common studio specs.

    ``max_cps`` characters/second, ``max_cpl`` characters per line, ``max_lines``
    lines per cue, and ``min_duration_ms`` / ``max_duration_ms`` cue duration
    bounds. Structural invariants are not configurable — they are always enforced.
    """

    max_cps: float = 17.0
    max_cpl: int = 42
    max_lines: int = 2
    min_duration_ms: int = 700
    max_duration_ms: int = 7000

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the active thresholds."""
        return {
            "max_cps": self.max_cps,
            "max_cpl": self.max_cpl,
            "max_lines": self.max_lines,
            "min_duration_ms": self.min_duration_ms,
            "max_duration_ms": self.max_duration_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckConfig:
        """Build a config from a partial dict, falling back to defaults per field."""
        defaults = cls()
        return cls(
            max_cps=data.get("max_cps", defaults.max_cps),
            max_cpl=data.get("max_cpl", defaults.max_cpl),
            max_lines=data.get("max_lines", defaults.max_lines),
            min_duration_ms=data.get("min_duration_ms", defaults.min_duration_ms),
            max_duration_ms=data.get("max_duration_ms", defaults.max_duration_ms),
        )


@dataclass(frozen=True)
class Violation:
    """One rule breach found against one cue (or cue pair)."""

    rule: RuleId
    severity: Severity
    cue_index: int
    message: str
    observed: float | int | str | None = None
    limit: float | int | str | None = None

    @property
    def is_structural(self) -> bool:
        """True when this violation breaks a structural invariant."""
        return self.rule in STRUCTURAL_RULES

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the violation."""
        return {
            "rule": str(self.rule),
            "severity": str(self.severity),
            "cue_index": self.cue_index,
            "message": self.message,
            "observed": self.observed,
            "limit": self.limit,
        }


@dataclass(frozen=True)
class CheckReport:
    """The full result of checking a document: every violation, plus rollups."""

    violations: list[Violation] = field(default_factory=list)
    cue_count: int = 0

    @property
    def ok(self) -> bool:
        """True when there are no violations at all."""
        return not self.violations

    @property
    def has_errors(self) -> bool:
        """True when at least one structural ``ERROR`` violation is present."""
        return any(v.severity is Severity.ERROR for v in self.violations)

    def flagged_indices(self) -> list[int]:
        """The sorted, de-duplicated cue indices that have any violation.

        These are the cues the corrector targets — only flagged cues are sent to
        the LLM, keeping token use proportional to the problems found.
        """
        return sorted({v.cue_index for v in self.violations})

    def for_cue(self, index: int) -> list[Violation]:
        """Every violation recorded against a given cue index, in rule order."""
        return [v for v in self.violations if v.cue_index == index]

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary (counts by severity + the violation list)."""
        errors = sum(1 for v in self.violations if v.severity is Severity.ERROR)
        warnings = sum(1 for v in self.violations if v.severity is Severity.WARNING)
        return {
            "ok": self.ok,
            "cue_count": self.cue_count,
            "summary": {
                "errors": errors,
                "warnings": warnings,
                "total": len(self.violations),
            },
            "violations": [v.as_dict() for v in self.violations],
        }


# Deterministic sort key: structural-first, then by cue index, then by rule id.
# Rule order within a family follows the RuleId declaration order.
_RULE_ORDER: dict[RuleId, int] = {rule: i for i, rule in enumerate(RuleId)}


def _sort_key(v: Violation) -> tuple[int, int, int]:
    return (0 if v.is_structural else 1, v.cue_index, _RULE_ORDER[v.rule])


def structural_violations(subs: Subtitles) -> list[Violation]:
    """Check only the structural invariants (index, ordering, overlap, duration).

    Used both as part of :func:`check` and standalone by the corrector to
    re-validate an edited document before it is emitted.
    """
    violations: list[Violation] = []
    prev: Cue | None = None
    for position, cue in enumerate(subs.cues):
        expected_index = position + 1
        if cue.index != expected_index:
            violations.append(
                Violation(
                    rule=RuleId.INDEX_CONTINUITY,
                    severity=Severity.ERROR,
                    cue_index=cue.index,
                    message=(
                        f"Cue index {cue.index} breaks continuity; expected {expected_index}."
                    ),
                    observed=cue.index,
                    limit=expected_index,
                )
            )
        if cue.duration_ms <= 0:
            violations.append(
                Violation(
                    rule=RuleId.NEGATIVE_DURATION,
                    severity=Severity.ERROR,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} has non-positive duration "
                        f"({cue.duration_ms} ms): end must be after start."
                    ),
                    observed=cue.duration_ms,
                    limit=1,
                )
            )
        if prev is not None:
            if cue.start_ms <= prev.start_ms:
                violations.append(
                    Violation(
                        rule=RuleId.TIMING_ORDER,
                        severity=Severity.ERROR,
                        cue_index=cue.index,
                        message=(
                            f"Cue {cue.index} start ({cue.start_ms} ms) is not after "
                            f"previous cue start ({prev.start_ms} ms)."
                        ),
                        observed=cue.start_ms,
                        limit=prev.start_ms,
                    )
                )
            if cue.start_ms < prev.end_ms:
                violations.append(
                    Violation(
                        rule=RuleId.TIMING_OVERLAP,
                        severity=Severity.ERROR,
                        cue_index=cue.index,
                        message=(
                            f"Cue {cue.index} starts ({cue.start_ms} ms) before the "
                            f"previous cue ends ({prev.end_ms} ms)."
                        ),
                        observed=cue.start_ms,
                        limit=prev.end_ms,
                    )
                )
        prev = cue
    return sorted(violations, key=_sort_key)


def readability_violations(subs: Subtitles, config: CheckConfig) -> list[Violation]:
    """Check only the configurable readability thresholds (CPS, CPL, lines, duration)."""
    violations: list[Violation] = []
    for cue in subs.cues:
        duration = cue.duration_ms
        if duration > 0 and duration < config.min_duration_ms:
            violations.append(
                Violation(
                    rule=RuleId.MIN_DURATION,
                    severity=Severity.WARNING,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} duration {duration} ms is below the "
                        f"minimum of {config.min_duration_ms} ms."
                    ),
                    observed=duration,
                    limit=config.min_duration_ms,
                )
            )
        if duration > config.max_duration_ms:
            violations.append(
                Violation(
                    rule=RuleId.MAX_DURATION,
                    severity=Severity.WARNING,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} duration {duration} ms exceeds the "
                        f"maximum of {config.max_duration_ms} ms."
                    ),
                    observed=duration,
                    limit=config.max_duration_ms,
                )
            )
        cps = cue.cps()
        if cps > config.max_cps:
            violations.append(
                Violation(
                    rule=RuleId.CPS,
                    severity=Severity.WARNING,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} reads at {cps:.1f} chars/sec, over the "
                        f"limit of {config.max_cps:.1f}."
                    ),
                    observed=round(cps, 2),
                    limit=config.max_cps,
                )
            )
        longest = max((len(line) for line in cue.lines), default=0)
        if longest > config.max_cpl:
            violations.append(
                Violation(
                    rule=RuleId.CPL,
                    severity=Severity.WARNING,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} has a line of {longest} chars, over the "
                        f"per-line limit of {config.max_cpl}."
                    ),
                    observed=longest,
                    limit=config.max_cpl,
                )
            )
        if len(cue.lines) > config.max_lines:
            violations.append(
                Violation(
                    rule=RuleId.MAX_LINES,
                    severity=Severity.WARNING,
                    cue_index=cue.index,
                    message=(
                        f"Cue {cue.index} has {len(cue.lines)} lines, over the "
                        f"limit of {config.max_lines}."
                    ),
                    observed=len(cue.lines),
                    limit=config.max_lines,
                )
            )
    return sorted(violations, key=_sort_key)


def check(subs: Subtitles, config: CheckConfig | None = None) -> CheckReport:
    """Run every rule check and return the combined :class:`CheckReport`.

    Structural violations are listed before readability ones; within each family
    violations are ordered by cue index then rule id, so output is deterministic.
    ``config`` defaults to :class:`CheckConfig` defaults when ``None``.
    """
    cfg = config or CheckConfig()
    combined = structural_violations(subs) + readability_violations(subs, cfg)
    ordered = sorted(combined, key=_sort_key)
    return CheckReport(violations=ordered, cue_count=len(subs))
