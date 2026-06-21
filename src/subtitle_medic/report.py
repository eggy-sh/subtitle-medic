"""Human (Markdown) and machine (JSON) rendering of check and correction results.

Two consumers: the ``check`` command renders a :class:`~subtitle_medic.rules.CheckReport`,
and the ``fix`` command renders a :class:`~subtitle_medic.corrector.CorrectionResult`
(the before/after edits plus the pre- and post-correction reports). Markdown is
for the ``--report out.md`` artifact a human reviews; the ``*_json`` variants
produce the single JSON object the CLI prints under ``--json`` for automation.

Pure formatting — no I/O beyond returning strings/dicts, no LLM.
"""

from __future__ import annotations

from typing import Any

from .corrector import CorrectionResult
from .rules import CheckReport, Severity


def _counts(report: CheckReport) -> tuple[int, int]:
    errors = sum(1 for v in report.violations if v.severity is Severity.ERROR)
    warnings = sum(1 for v in report.violations if v.severity is Severity.WARNING)
    return errors, warnings


def check_report_markdown(report: CheckReport, *, source: str = "") -> str:
    """Render a :class:`CheckReport` as a Markdown QA report.

    Includes a summary line (cue count, error/warning counts), a per-rule rollup,
    and a table of every violation (cue index, rule, severity, observed vs limit,
    message). ``source`` is an optional file label for the heading.
    """
    errors, warnings = _counts(report)
    heading = "# Caption QA report"
    if source:
        heading += f": {source}"
    lines = [heading, ""]
    status = "PASS" if report.ok else "FAIL"
    lines.append(
        f"**Summary:** {status} — {report.cue_count} cues, {errors} errors, {warnings} warnings."
    )
    lines.append("")
    if report.ok:
        lines.append("No violations found.")
        return "\n".join(lines) + "\n"
    lines.append("| Cue | Rule | Severity | Observed | Limit | Message |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for v in report.violations:
        observed = "" if v.observed is None else str(v.observed)
        limit = "" if v.limit is None else str(v.limit)
        message = v.message.replace("|", "\\|")
        lines.append(
            f"| {v.cue_index} | {v.rule} | {v.severity} | {observed} | {limit} | {message} |"
        )
    return "\n".join(lines) + "\n"


def check_report_json(report: CheckReport, *, source: str = "") -> dict[str, Any]:
    """Build the JSON object the ``check --json`` command prints (exactly one object)."""
    errors, warnings = _counts(report)
    return {
        "source": source,
        "ok": report.ok,
        "has_errors": report.has_errors,
        "cue_count": report.cue_count,
        "summary": {
            "errors": errors,
            "warnings": warnings,
            "total": len(report.violations),
        },
        "flagged_indices": report.flagged_indices(),
        "violations": [v.as_dict() for v in report.violations],
    }


def correction_report_markdown(result: CorrectionResult, *, source: str = "") -> str:
    """Render a :class:`CorrectionResult` as a Markdown correction report.

    Shows each applied edit as a before/after diff, the glossary terms enforced,
    the residual violations after correction, and the run's token/cost telemetry.
    """
    heading = "# Caption correction report"
    if source:
        heading += f": {source}"
    lines = [heading, ""]
    mode = "LLM" if result.used_model else "no-LLM (deterministic)"
    lines.append(
        f"**Mode:** {mode} — {result.applied_count} of {len(result.edits)} candidate cues edited."
    )
    valid = "valid" if result.structurally_valid else "INVALID"
    lines.append(f"**Structural status:** {valid}.")
    lines.append("")

    if result.edits:
        lines.append("## Edits")
        lines.append("")
        for edit in result.edits:
            status = "applied" if edit.changed else "no change"
            lines.append(f"### Cue {edit.cue_index} ({status})")
            lines.append("")
            lines.append("Before:")
            lines.append("```")
            lines.extend(edit.before)
            lines.append("```")
            lines.append("After:")
            lines.append("```")
            lines.extend(edit.after)
            lines.append("```")
            lines.append("")
    else:
        lines.append("No cues required correction.")
        lines.append("")

    if result.post_report is not None:
        residual = result.post_report.violations
        lines.append("## Residual violations after correction")
        lines.append("")
        if not residual:
            lines.append("None.")
        else:
            for v in residual:
                lines.append(f"- Cue {v.cue_index}: {v.rule} ({v.severity}) — {v.message}")
        lines.append("")

    if result.telemetry is not None:
        tel = result.telemetry.as_dict()
        lines.append("## Telemetry")
        lines.append("")
        lines.append(
            f"- Calls: {tel['calls']}; input tokens: {tel['total_input_tokens']}; "
            f"output tokens: {tel['total_output_tokens']}; "
            f"estimated cost: ${tel['total_cost_usd']:.6f}"
        )
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def correction_report_json(result: CorrectionResult, *, source: str = "") -> dict[str, Any]:
    """Build the JSON object the ``fix --json`` command prints (exactly one object)."""
    payload: dict[str, Any] = {
        "source": source,
        "used_model": result.used_model,
        "applied_count": result.applied_count,
        "structurally_valid": result.structurally_valid,
        "edits": [edit.as_dict() for edit in result.edits],
        "pre_report": (
            check_report_json(result.pre_report, source=source)
            if result.pre_report is not None
            else None
        ),
        "post_report": (
            check_report_json(result.post_report, source=source)
            if result.post_report is not None
            else None
        ),
        "telemetry": (result.telemetry.as_dict() if result.telemetry is not None else None),
    }
    return payload
