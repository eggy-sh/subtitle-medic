# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

_Nothing yet._

## [0.1.0] — 2026-06-20

First release: a complete, deterministic caption QA + cue-scoped correction tool
for SRT and WebVTT, built on [`replykit`](https://github.com/eggy-sh/replykit).
All modules below are fully implemented and exercised by a hermetic test suite
(no network, no live LLM) at 98.89% line+branch coverage.

### Added

- **Cue model** (`model`): format-agnostic `Cue` / `Subtitles` containers and the
  `CaptionFormat` enum (SRT / VTT). Cues are immutable; `Cue.with_lines` and
  `Subtitles.with_cues` return copies, so corrections can never mutate timing,
  index, or count in place.
- **Parser** (`parser`): round-trip-safe `loads` / `dumps` / `load` / `dump` for
  SRT and WebVTT, body-sniffing `detect_format`, `parse_timecode` /
  `format_timecode`, and a `ParseError` that names the failing cue and reason.
  Tolerant on input (CRLF, BOM, trailing whitespace), canonical on output;
  WebVTT headers, `NOTE` blocks, and per-cue settings (e.g.
  `align:start position:10%`) are preserved verbatim across a round trip.
- **Rule engine** (`rules`): `check`, `structural_violations`,
  `readability_violations`, plus `CheckConfig`, `CheckReport`, `Violation`,
  `RuleId`, `Severity`, and `STRUCTURAL_RULES`. Structural invariants (contiguous
  indices, monotonic non-overlapping timing, positive duration) are always-on
  `ERROR`s; readability thresholds (CPS / CPL / max-lines / min-dur / max-dur) are
  configurable `WARNING`s.
- **Glossary** (`glossary`): `Glossary`, `parse_glossary`, `load_glossary`, and the
  shared `EMPTY_GLOSSARY`. Supports bare canonical terms and `wrong => Right`
  mappings, with punctuation-aware token canonicalization.
- **Corrector** (`corrector`): `Corrector`, `CorrectionResult`, `CueEdit` —
  cue-scoped, timing-preserving corrections driven through a replykit `Agent` and
  a single `edit_cue` tool whose only argument is the new text (timing and index
  are not parameters, so the model structurally cannot desync captions). Includes
  a hard structural re-validation gate after every edit, telemetry capture, and a
  graceful no-LLM mode that applies only deterministic glossary/whitespace fixes
  when no model is configured.
- **Reporting** (`report`): Markdown and JSON renderers for both check and
  correction results (`check_report_markdown` / `check_report_json` /
  `correction_report_markdown` / `correction_report_json`).
- **CLI** (`cli`): the `subtitle-medic check` and `fix` Typer commands. Each takes
  `--json` and prints exactly one JSON object to stdout; on failure it prints
  `{"ok": false, "error": ...}`. Exit-code contract: `0` ok, `1` structural
  breakage / emit gate, `2` usage error. `fix` supports `--glossary`, `--model`,
  `-o/--output`, and `--report`, and never writes an output file whose structural
  invariants are broken.
- **Packaging**: PEP 621 `pyproject.toml` (hatchling, `src/` layout,
  `subtitle_medic` import package), the `subtitle-medic` console script,
  `replykit` / `typer` / `rich` runtime deps, and `anthropic` / `openai` /
  `ollama` / `dev` extras (provider SDKs are surfaced through replykit's extras;
  the core import and rule checks never need them).
- **Tests & fixtures**: hermetic suite with `tests/conftest.py` fixtures
  (valid/broken SRT, valid VTT, sample glossary) plus runnable `examples/`
  (`check_demo.py`, `fix_demo.py`, `sample.srt`, `sample.vtt`, `glossary.txt`).
- **Docs**: README, CONTRIBUTING, and an MIT LICENSE (2026 Edgar Hernandez).
- **CI**: GitHub Actions matrix (Python 3.11 / 3.12) running `ruff check`,
  `ruff format --check`, pytest with coverage, and a hermetic CLI/examples smoke
  test.

[Unreleased]: https://github.com/eggy-sh/subtitle-medic/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/eggy-sh/subtitle-medic/releases/tag/v0.1.0
