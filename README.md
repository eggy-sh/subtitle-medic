# subtitle-medic

Caption QA and **cue-scoped** correction for SRT and WebVTT — a deterministic
rule engine plus an LLM that only ever edits the *text* of a flagged cue, never
its timing, index, or count. Built on [`replykit`](https://github.com/eggy-sh/replykit),
the provider-agnostic agent I/O engine, so the same tool runs against a hosted
model, a local edge model, or **no model at all**.

```bash
subtitle-medic check episode.srt
subtitle-medic fix episode.srt --glossary brand.txt -o episode.fixed.srt --report qa.md
```

Every command takes `--json` and prints **exactly one JSON object** to stdout —
nothing else — so it drops straight into a pipeline:

```bash
subtitle-medic check episode.srt --json | jq '.summary.errors'
```

> Part of the Post-Production Agent Kit: `subtitle-medic`, `cutlist`, and
> `conforma` all run on the one shared `replykit` core.

## What it does

`subtitle-medic` parses a caption file into a format-agnostic cue model, runs two
families of checks, and (on `fix`) repairs the flagged cues:

1. **Structural invariants** — always enforced, never configurable: contiguous
   indices, monotonic non-overlapping timing, and positive durations. A file
   that breaks these is *broken*, and the tool will not emit one.
2. **Readability thresholds** — configurable studio limits: characters-per-second
   (CPS), characters-per-line (CPL), max lines per cue, and min/max cue duration.

`fix` then sends **only the flagged cues** to the model (token use stays
proportional to the number of problems, not the length of the file), enforces
your glossary's canonical spellings, and rebalances line breaks. Crucially, the
model can only call a single `edit_cue` tool whose *only* argument is the new
text — timing and index are not parameters, so the model **structurally cannot**
desync the captions. After every edit the whole document is re-validated against
the structural invariants; any edit that would break one is rejected and the
original cue is kept.

## Why it's different (for studios)

Caption houses and post-production teams already have spell-checkers and linters.
What they don't have is a corrector that is *safe to run unattended on a
deliverable*:

- **Timing is immutable by construction, not by prompt.** The LLM never sees or
  edits timecodes. "Don't change the timing" isn't an instruction it might
  ignore — it's not an argument it can pass. Frame-accurate sync survives every
  edit.
- **A hard structural gate before anything is written.** The corrected document
  is re-checked against the structural invariants and the file is emitted *only*
  if it is clean. The exit code is non-zero otherwise, so it fails loud in CI
  rather than shipping a desynced SRT.
- **Round-trip fidelity.** `dumps(loads(text))` equals the normalized input and
  `loads(dumps(subs))` equals the document — WebVTT headers, `NOTE` blocks, and
  per-cue settings (`align:start position:10%`) are preserved verbatim. The tool
  touches the words you flagged and leaves the rest byte-stable.
- **A glossary that's enforced, not suggested.** Brand and product spellings
  (`GitHub`, `Kubernetes`) are injected once into the prompt and verified on the
  way out, so a course on Kubernetes never ships "kuberntes."
- **Per-run token / cost / repair telemetry**, surfaced from `replykit`. You see
  what each correction pass cost — actionable for a house running thousands of
  cues a week — and unknown/local models price at `$0.00` so an edge model never
  inflates the numbers.
- **Runs with no model.** Point it at no provider and `fix` degrades to a safe,
  deterministic pass. The QA half (`check`) never needs a model at all and has
  **zero third-party runtime cost** beyond the CLI shell.

## Install

```bash
pip install subtitle-medic                 # CLI + deterministic checks, hermetic
pip install 'subtitle-medic[anthropic]'    # + Anthropic adapter for `fix --model anthropic`
pip install 'subtitle-medic[openai]'       # + OpenAI adapter
pip install 'subtitle-medic[ollama]'       # + local Ollama (edge / offline) adapter
pip install 'subtitle-medic[dev]'          # pytest, pytest-cov, ruff
```

The provider extras are thin pass-throughs to `replykit`'s extras. The core
import and the entire `check` path need none of them.

## Quickstart

```bash
# 1. QA a file — exits non-zero if a structural invariant is broken.
subtitle-medic check examples/sample.srt

# 2. Tighten the readability thresholds to your house spec.
subtitle-medic check examples/sample.srt --max-cps 15 --max-cpl 40 --max-lines 2

# 3. Fix it. With no --model this is the safe, deterministic, no-LLM pass.
subtitle-medic fix examples/sample.srt \
  --glossary examples/glossary.txt \
  --output examples/sample.fixed.srt \
  --report qa.md

# 4. Fix with a model (needs the matching extra + that provider's API key).
subtitle-medic fix examples/sample.srt --glossary examples/glossary.txt \
  --model anthropic -o out.srt
```

### `--json` for automation

```bash
subtitle-medic check examples/sample.srt --json | jq '{cues: .cue_count, errors: .summary.errors}'
subtitle-medic fix   examples/sample.srt --json | jq '.applied_count'
```

In `--json` mode the single stdout object is the whole result; on failure it is
`{"ok": false, "error": "..."}` so a pipeline always gets one parseable object.
Human-mode diagnostics go to **stderr**, keeping stdout clean.

### Library use (hermetic, no network)

The correction engine accepts any `replykit` model, so you can drive it in tests
or scripts with a scripted/mock model and never touch the network:

```python
from replykit import ScriptedModel
from subtitle_medic.parser import loads
from subtitle_medic.glossary import parse_glossary
from subtitle_medic.corrector import Corrector

subs = loads(open("episode.srt").read())
gloss = parse_glossary("GitHub\nkuberntes => Kubernetes\n")

# No model at all -> safe deterministic pass.
result = Corrector(None, glossary=gloss).correct(subs)
assert result.structurally_valid          # the hard emit gate
print(result.applied_count, "edit(s)")
```

See [`examples/`](examples/) for runnable, fully offline demos:

```bash
python examples/check_demo.py   # deterministic QA, Markdown + JSON output
python examples/fix_demo.py     # cue-scoped correction with a hermetic ScriptedModel
```

## Commands

### `check FILE`

Parse and report every rule violation. Flags: `--json`, `--max-cps`, `--max-cpl`,
`--max-lines`, `--min-dur`, `--max-dur`.

### `fix FILE`

Apply cue-scoped corrections and write the result. Flags: `--glossary`,
`--report` (Markdown artifact), `--output/-o` (corrected captions; without it the
corrected text prints to stdout in human mode), `--model`
(`anthropic`|`openai`|`ollama`; empty = no-LLM), plus the same `--json` and
threshold flags as `check`.

### Exit codes

| Code | Meaning |
|------|---------|
| `0`  | Success. For `check`, no structural ERROR violations were found. |
| `1`  | `check`: a structural invariant is broken. `fix`: the correction would break a structural invariant, so **nothing was written**. |
| `2`  | Usage error: missing file, unparseable caption, bad `--model`, or unwritable output/report. |

## Glossary format

One term per line; `#` lines and blanks are ignored. Two shapes:

```
GitHub
kuberntes => Kubernetes
```

A bare term is the canonical spelling; `wrong => Right` is an explicit mapping.

## Interop & formats

- **SRT and WebVTT**, auto-detected: a leading `WEBVTT` signature (after an
  optional BOM/whitespace) selects VTT; otherwise SRT.
- **Timecodes** parse to integer milliseconds and re-emit in the canonical
  per-format spelling — SRT `HH:MM:SS,mmm` (comma), VTT `HH:MM:SS.mmm` (dot). The
  parser accepts either separator and an optional hours field on input.
- **Tolerant in, canonical out.** Input may have CRLF, a BOM, trailing
  whitespace, or blank-line padding; output is always LF with a single blank line
  between cues. Structurally broken input (bad timecodes, missing `-->`) raises a
  `ParseError` that names *where* it broke — the CLI surfaces that as exit code 2.
- **WebVTT preservation.** Header lines (`Kind: captions`, `NOTE`/`STYLE` blocks)
  and per-cue settings are carried through verbatim so a VTT round-trips.
- **Round-trip guarantee.** `dumps(loads(text)) == normalize(text)` for
  well-formed input and `loads(dumps(subs)) == subs`, so the corrected file
  differs from the original only in the cues you actually changed.

## Development

```bash
uv venv && uv pip install -e '/path/to/replykit' && uv pip install -e '.[dev]'
uv run ruff check . && uv run ruff format --check .
uv run pytest --cov=subtitle_medic --cov-report=term-missing
```

The whole suite is **hermetic** — no network, no live LLM (correction tests use
`replykit`'s `ScriptedModel`). See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE). Copyright (c) 2026 Edgar Hernandez.
