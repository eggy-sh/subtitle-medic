"""The ``subtitle-medic`` command-line interface (Typer + Rich).

Two commands, both automation-friendly via ``--json`` (each prints exactly one
JSON object to stdout and nothing else, so the CLI drops into agent pipelines):

* ``subtitle-medic check FILE`` — parse + run rule checks, report violations.
* ``subtitle-medic fix FILE --glossary g.txt --report out.md`` — apply cue-scoped
  corrections, re-validate structural invariants, write the corrected file and a
  Markdown report.

The CLI is the **only** module that wires a concrete replykit provider adapter
from a flag (``--model``); with no provider configured, ``fix`` degrades to the
graceful no-LLM rules-only path. This module is owned by SWE-CLI; it imports the
library's public surface from :mod:`subtitle_medic` (plus replykit's adapter
*names*, imported lazily) and adds no new core logic.

Exit codes (the automation contract):

* ``0`` — success; for ``check`` this also means no structural ERROR violations.
* ``1`` — structural errors were found (``check``) or the corrected document
  would break a structural invariant, so nothing was written (``fix``).
* ``2`` — a user/usage error: missing file, parse failure, bad ``--model``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from .corrector import Corrector
from .glossary import EMPTY_GLOSSARY, load_glossary
from .parser import ParseError, dumps, load
from .report import (
    check_report_json,
    correction_report_json,
    correction_report_markdown,
)
from .rules import CheckConfig, Severity
from .rules import check as run_check

app = typer.Typer(
    name="subtitle-medic",
    help="Caption QA + cue-scoped correction for SRT and WebVTT.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()
err_console = Console(stderr=True)

# Exit codes (kept symbolic so call sites read clearly).
EXIT_OK = 0
EXIT_STRUCTURAL = 1
EXIT_USAGE = 2


# ---------------------------------------------------------------------------
# Small output / error helpers (the --json contract lives here).
# ---------------------------------------------------------------------------


def _emit_json(payload: dict[str, Any]) -> None:
    """Print exactly one JSON object to stdout — nothing else.

    Uses ``json.dumps`` + a plain ``print`` (not Rich) so the output is byte-clean
    for ``jq`` and never carries ANSI styling, even when stdout is a TTY.
    """
    print(json.dumps(payload))


def _fail(message: str, *, as_json: bool, code: int = EXIT_USAGE) -> None:
    """Report a failure and exit.

    In ``--json`` mode the single stdout object is ``{"ok": false, "error": ...}``
    so a pipeline still gets one parseable object; otherwise a Rich error line is
    written to **stderr** (stdout stays clean).
    """
    if as_json:
        _emit_json({"ok": False, "error": message})
    else:
        err_console.print(f"[bold red]error:[/bold red] {message}")
    raise typer.Exit(code)


def _build_config(
    max_cps: float,
    max_cpl: int,
    max_lines: int,
    min_duration_ms: int,
    max_duration_ms: int,
) -> CheckConfig:
    """Assemble a :class:`CheckConfig` from the shared threshold options."""
    return CheckConfig(
        max_cps=max_cps,
        max_cpl=max_cpl,
        max_lines=max_lines,
        min_duration_ms=min_duration_ms,
        max_duration_ms=max_duration_ms,
    )


def _build_model(spec: str) -> Any | None:
    """Resolve ``--model`` into a concrete replykit model, or ``None`` for no-LLM.

    An empty spec (the default) selects the graceful no-LLM mode. ``anthropic`` /
    ``openai`` / ``ollama`` import their replykit adapter lazily, so a missing SDK
    surfaces as a clear, actionable message rather than an import crash. The
    hermetic ``mock`` / ``scripted`` backends are intentionally **not** exposed on
    the CLI — tests drive the corrector with those directly.

    Raises :class:`ValueError` with a user-facing message on an unknown spec or a
    missing provider SDK; the caller turns that into an exit-code-2 failure.
    """
    name = spec.strip().lower()
    if not name:
        return None

    # The provider adapters and the missing-dep error live in replykit; import
    # the names lazily so a missing optional SDK only bites when actually asked
    # for, and never at CLI import time.
    from replykit import (
        AnthropicModel,
        MissingDependencyError,
        OllamaModel,
        OpenAIModel,
    )

    try:
        if name == "anthropic":
            return AnthropicModel()
        if name == "openai":
            return OpenAIModel()
        if name == "ollama":
            # Ollama has no default model id; pick a small, common local default.
            return OllamaModel("llama3")
    except MissingDependencyError as exc:
        raise ValueError(str(exc)) from exc

    raise ValueError(
        f"unknown --model {spec!r}; choose anthropic | openai | ollama, "
        "or omit it for no-LLM (rules-only) mode"
    )


def _load_subs(file: str, *, as_json: bool):
    """Load + parse a caption file, mapping I/O and parse failures to exit code 2."""
    try:
        return load(file)
    except FileNotFoundError:
        _fail(f"no such file: {file}", as_json=as_json)
    except ParseError as exc:
        _fail(f"could not parse {file}: {exc}", as_json=as_json)
    except OSError as exc:
        _fail(f"could not read {file}: {exc}", as_json=as_json)


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@app.command()
def check(
    file: str = typer.Argument(..., help="Path to an .srt or .vtt caption file."),
    json_out: bool = typer.Option(False, "--json", help="Emit one JSON object to stdout."),
    max_cps: float = typer.Option(17.0, "--max-cps", help="Max characters per second."),
    max_cpl: int = typer.Option(42, "--max-cpl", help="Max characters per line."),
    max_lines: int = typer.Option(2, "--max-lines", help="Max lines per cue."),
    min_duration_ms: int = typer.Option(700, "--min-dur", help="Min cue duration (ms)."),
    max_duration_ms: int = typer.Option(7000, "--max-dur", help="Max cue duration (ms)."),
) -> None:
    """Parse FILE and report every rule violation. Exit non-zero on structural errors."""
    subs = _load_subs(file, as_json=json_out)
    config = _build_config(max_cps, max_cpl, max_lines, min_duration_ms, max_duration_ms)
    report = run_check(subs, config)

    if json_out:
        _emit_json(check_report_json(report, source=file))
    else:
        _render_check(report, source=file)

    if report.has_errors:
        raise typer.Exit(EXIT_STRUCTURAL)


def _render_check(report, *, source: str) -> None:
    """Pretty-print a check report to stdout with Rich (human mode)."""
    errors = sum(1 for v in report.violations if v.severity == Severity.ERROR)
    warnings = len(report.violations) - errors

    console.print(f"[bold]{source}[/bold] — {report.cue_count} cue(s)")
    if report.ok:
        console.print("[bold green]clean[/bold green]: no violations.")
        return

    summary = f"[red]{errors} error(s)[/red], [yellow]{warnings} warning(s)[/yellow]"
    console.print(summary)

    table = Table(title="Violations")
    table.add_column("cue", justify="right")
    table.add_column("rule")
    table.add_column("severity")
    table.add_column("observed", justify="right")
    table.add_column("limit", justify="right")
    table.add_column("message")
    for v in report.violations:
        sev = "[red]error[/red]" if v.severity == Severity.ERROR else "[yellow]warning[/yellow]"
        table.add_row(
            str(v.cue_index),
            str(v.rule),
            sev,
            "" if v.observed is None else str(v.observed),
            "" if v.limit is None else str(v.limit),
            v.message,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


@app.command()
def fix(
    file: str = typer.Argument(..., help="Path to an .srt or .vtt caption file."),
    glossary: str = typer.Option("", "--glossary", help="Path to a glossary file."),
    report_path: str = typer.Option("", "--report", help="Write a Markdown report to this path."),
    output: str = typer.Option(
        "", "--output", "-o", help="Write corrected captions here (default: print to stdout)."
    ),
    model: str = typer.Option(
        "", "--model", help="replykit provider (anthropic/openai/ollama); empty = no-LLM mode."
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit one JSON object to stdout."),
    max_cps: float = typer.Option(17.0, "--max-cps", help="Max characters per second."),
    max_cpl: int = typer.Option(42, "--max-cpl", help="Max characters per line."),
    max_lines: int = typer.Option(2, "--max-lines", help="Max lines per cue."),
    min_duration_ms: int = typer.Option(700, "--min-dur", help="Min cue duration (ms)."),
    max_duration_ms: int = typer.Option(7000, "--max-dur", help="Max cue duration (ms)."),
) -> None:
    """Apply cue-scoped corrections to FILE, re-validate invariants, write the result."""
    subs = _load_subs(file, as_json=json_out)
    config = _build_config(max_cps, max_cpl, max_lines, min_duration_ms, max_duration_ms)

    # Resolve the optional glossary.
    if glossary:
        try:
            gloss = load_glossary(glossary)
        except FileNotFoundError:
            _fail(f"no such glossary: {glossary}", as_json=json_out)
        except OSError as exc:
            _fail(f"could not read glossary {glossary}: {exc}", as_json=json_out)
    else:
        gloss = EMPTY_GLOSSARY

    # Resolve the model (None => graceful no-LLM mode).
    try:
        backend = _build_model(model)
    except ValueError as exc:
        _fail(str(exc), as_json=json_out)
        return  # _fail raises, but keeps type-checkers happy.

    corrector = Corrector(backend, glossary=gloss, config=config)
    result = corrector.correct(subs)

    # Hard gate: never emit a structurally-broken document.
    if not result.structurally_valid:
        if json_out:
            payload = correction_report_json(result, source=file)
            payload["ok"] = False
            payload["error"] = "correction would break a structural invariant; nothing written"
            _emit_json(payload)
        else:
            err_console.print(
                "[bold red]error:[/bold red] correction would break a structural "
                "invariant; nothing written."
            )
        raise typer.Exit(EXIT_STRUCTURAL)

    # Serialize the corrected document.
    corrected_text = dumps(result.subtitles)

    # Write the Markdown report artifact, if requested.
    if report_path:
        try:
            with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(correction_report_markdown(result, source=file))
        except OSError as exc:
            _fail(f"could not write report {report_path}: {exc}", as_json=json_out)

    # Write the corrected captions to --output, if requested.
    if output:
        try:
            with open(output, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(corrected_text)
        except OSError as exc:
            _fail(f"could not write output {output}: {exc}", as_json=json_out)

    if json_out:
        payload = correction_report_json(result, source=file)
        payload.setdefault("ok", True)
        payload["output"] = output or None
        payload["report"] = report_path or None
        _emit_json(payload)
        return

    _render_fix(result, source=file, output=output, report_path=report_path)

    # If no --output was given, emit the corrected text to stdout so the command
    # is still useful as a filter (human mode only; --json stays one object).
    if not output:
        console.rule("[bold]corrected captions[/bold]")
        sys.stdout.write(corrected_text)
        if not corrected_text.endswith("\n"):
            sys.stdout.write("\n")


def _render_fix(result, *, source: str, output: str, report_path: str) -> None:
    """Pretty-print a correction result to stdout with Rich (human mode)."""
    mode = "LLM" if result.used_model else "no-LLM (rules only)"
    console.print(
        f"[bold]{source}[/bold] — {result.applied_count} edit(s) applied [dim]({mode})[/dim]"
    )

    changed = [e for e in result.edits if e.changed]
    if changed:
        table = Table(title="Applied edits")
        table.add_column("cue", justify="right")
        table.add_column("before")
        table.add_column("after")
        for edit in changed:
            table.add_row(
                str(edit.cue_index),
                "\\n".join(edit.before),
                "\\n".join(edit.after),
            )
        console.print(table)
    else:
        console.print("[dim]no text changes were applied.[/dim]")

    if result.post_report is not None:
        residual = len(result.post_report.violations)
        if residual:
            console.print(f"[yellow]{residual} residual violation(s) remain.[/yellow]")
        else:
            console.print("[green]no residual violations.[/green]")

    if output:
        console.print(f"[dim]wrote corrected captions -> {output}[/dim]")
    if report_path:
        console.print(f"[dim]wrote report -> {report_path}[/dim]")


def main() -> None:
    """Console-script entry point (``subtitle-medic``)."""
    app()


if __name__ == "__main__":
    main()
