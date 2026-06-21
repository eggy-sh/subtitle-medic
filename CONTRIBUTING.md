# Contributing to subtitle-medic

`subtitle-medic` is a small, dependency-light caption QA tool built on the
`replykit` engine. The bar for contributions is correctness, **hermetic** tests,
and a clean public surface.

## Development setup

```bash
uv venv
uv pip install -e '/path/to/replykit'    # the engine, installed editable
uv pip install -e '.[dev]'
```

`replykit>=0.1.0` is a hard dependency; install it from PyPI or editable from a
local checkout. The `dev` extra adds pytest, pytest-cov, and ruff.

## Quality gates (must pass before a PR)

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest --cov=subtitle_medic --cov-report=term-missing
```

- The whole test suite is **hermetic**: no live LLM, no network. Correction tests
  drive the corrector with `replykit`'s `ScriptedModel` / `MockModel` and never
  call a real provider.
- Line coverage must stay **>= 90%**.
- **Structural invariants are sacred.** Any change to the parser or corrector must
  keep round-trip stability and must never emit a document that breaks index
  continuity, timing order, or cue count. Add a regression test for any invariant
  you touch.

## File ownership (parallel development)

The v0.1 build is split into two non-overlapping ownership sets so two engineers
work concurrently without collisions:

- **SWE-Core** owns the library modules under `src/subtitle_medic/` (except
  `cli.py`) and their unit tests under `tests/` (except the CLI/integration
  tests).
- **SWE-CLI** owns `src/subtitle_medic/cli.py`, the integration tests, the CI
  workflow, the README body, and `examples/`.

Neither engineer edits `pyproject.toml` or the other's files. The module public
APIs in `src/subtitle_medic/__init__.py` are the contract both code against.

## Style

- Python 3.11+, `src/` layout, PEP 621 packaging.
- `ruff` is the linter and formatter; keep both green.
