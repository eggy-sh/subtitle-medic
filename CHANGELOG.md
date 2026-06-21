# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project scaffold: PEP 621 `pyproject.toml` (src/ layout, `subtitle_medic`
  import package), `replykit>=0.1.0` plus `typer` / `rich` runtime deps, the
  `subtitle-medic` console script, and `anthropic` / `openai` / `ollama` / `dev`
  extras.
- Module stubs with the v0.1 public API (signatures + docstrings, bodies pending):
  - `model`: `Cue`, `Subtitles`, `CaptionFormat` cue model.
  - `parser`: round-trip-safe `loads` / `dumps` / `load` / `dump`, timecode
    parsing, and format detection for SRT and WebVTT.
  - `rules`: `check`, `structural_violations`, `readability_violations`,
    `CheckConfig`, `CheckReport`, `Violation`, `RuleId`, `Severity`.
  - `glossary`: `Glossary`, `parse_glossary`, `load_glossary`.
  - `corrector`: `Corrector`, `CorrectionResult`, `CueEdit` — cue-scoped,
    timing-preserving LLM corrections on the replykit `Agent`, with a graceful
    no-LLM mode.
  - `report`: Markdown + JSON renderers for check and correction results.
  - `cli`: the `subtitle-medic check` / `fix` Typer commands (with `--json`).
- Hermetic test fixtures (`tests/conftest.py`): valid/broken SRT, valid VTT, and
  a sample glossary.
- Docs: README skeleton, CONTRIBUTING, MIT LICENSE (2026 Edgar Hernandez).
