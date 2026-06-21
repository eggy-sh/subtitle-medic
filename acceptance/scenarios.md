# subtitle-medic — Scenario-based Acceptance Suite

PM/QA acceptance scenarios for `subtitle-medic`: caption QA + **cue-scoped**
correction for SRT and WebVTT. Every scenario below is **end-to-end** (real CLI
or the documented public library surface), has a **single, machine-checkable
success criterion** (exact exit code, specific `--json` field values, or a
structural/round-trip invariant), and runs **hermetically** — no network, no live
LLM. Model-mode scenarios drive the corrector with replykit's `ScriptedModel`
exactly as `examples/fix_demo.py` does.

## Scope: capabilities under test

Enumerated from `README.md`, `subtitle-medic --help` (`check`, `fix`), and `src/`:

1. **Structural QA (`check`)** — index continuity, monotonic non-overlapping
   timing, positive duration. Structural breaches are `ERROR`s and drive exit 1.
2. **Readability QA (`check`)** — configurable CPS / CPL / max-lines /
   min-dur / max-dur thresholds. Breaches are `WARNING`s (exit 0).
3. **Format parsing & auto-detection** — SRT vs WebVTT sniffed from the body;
   tolerant in (CRLF, BOM, trailing whitespace), canonical out.
4. **Round-trip fidelity** — `dumps(loads(text))` is canonical; `loads(dumps())`
   is equal; WebVTT header / `NOTE` blocks / per-cue settings preserved verbatim.
5. **Deterministic no-LLM correction (`fix`)** — glossary canonicalization +
   whitespace normalization on *flagged* cues only, with no model.
6. **Glossary enforcement** — bare-term and `wrong => Right` mappings.
7. **Model-mode cue-scoped correction (`fix --model`)** — text-only edits via the
   single `edit_cue` tool; timing/index/count immutable by construction.
8. **Hard structural emit gate** — a corrected document that would break a
   structural invariant is never written; exits 1.
9. **`--json` automation contract** — every command prints exactly one JSON
   object to stdout; on failure `{"ok": false, "error": ...}`.
10. **Exit-code contract** — `0` ok, `1` structural, `2` usage.
11. **Telemetry** — per-run calls / tokens / cost; unknown/local models price at
    `$0.00`; no-LLM mode reports zero calls and zero cost.
12. **Markdown report artifact** (`fix --report out.md`).

## Evaluation method

**Pass/fail, deterministic.** Each scenario asserts an exact value (exit code,
JSON field, file content, or a structural invariant computed from the artifact).
A scenario PASSES iff *all* of its listed assertions hold; otherwise it FAILS.
There is no rubric and no LLM judge — every output of this tool is deterministic
by design (the model only ever edits *text* of flagged cues, and even that is
driven by a scripted model in these scenarios), so a numeric-threshold rubric
would be inappropriate. **Suite-level bar: 10/10 scenarios pass (100%).**

All scenarios run from the repo root with the repo venv
(`/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv`); use
`.venv/bin/subtitle-medic` and `.venv/bin/python`. Fixtures are
`examples/sample.srt`, `examples/sample.vtt`, `examples/glossary.txt`, plus
small inline temp files for the broken/malformed cases. No scenario adds a model
call beyond the hermetic `ScriptedModel`.

---

## SC-01 — `check` clean readability QA on a well-formed SRT (passfail)

**Capability:** Structural + readability QA; `--json` contract; exit codes.

**Setup / action:**
```bash
.venv/bin/subtitle-medic check examples/sample.srt --json
```

**Success criterion (all must hold):**
- Exit code `== 0` (no structural ERRORs; readability warnings do not fail).
- stdout parses as exactly one JSON object with `.has_errors == false`,
  `.cue_count == 3`, `.summary.errors == 0`, `.summary.warnings == 3`,
  `.summary.total == 3`.
- `.flagged_indices == [1, 3]`.
- `.violations[]` contains exactly the rules `max_duration` (cue 1, observed
  `8000`, limit `7000`), `cpl` (cue 1, observed `99`, limit `42`), and
  `min_duration` (cue 3, observed `300`, limit `700`); every violation has
  `.severity == "warning"`.

---

## SC-02 — `check` detects structural timing breakage and exits 1 (passfail)

**Capability:** Structural invariants; exit-code-1 contract.

**Setup:** write a temp SRT whose cue 2 starts before cue 1 (out-of-order *and*
overlapping):
```
1
00:00:05,000 --> 00:00:08,000
First.

2
00:00:01,000 --> 00:00:02,000
Out of order.
```
**Action:** `.venv/bin/subtitle-medic check <tmp>.srt --json`

**Success criterion (all must hold):**
- Exit code `== 1`.
- JSON `.has_errors == true`, `.ok == false`, `.summary.errors == 2`.
- `.violations[]` includes a `timing_order` ERROR (cue_index 2, observed `1000`,
  limit `5000`) and a `timing_overlap` ERROR (cue_index 2, observed `1000`,
  limit `8000`); both have `.severity == "error"`.

---

## SC-03 — `check` threshold flags re-classify a clean file (passfail)

**Capability:** Configurable readability thresholds; flag plumbing.

**Setup / action:** the VTT is clean at defaults (SC-08 baseline). Tighten CPL:
```bash
.venv/bin/subtitle-medic check examples/sample.vtt --max-cpl 10 --json
```

**Success criterion (all must hold):**
- Exit code `== 0` (the new breaches are warnings, not structural errors).
- JSON `.ok == false`, `.has_errors == false`, `.summary.warnings == 3`,
  `.summary.errors == 0`.
- Every violation in `.violations[]` has `.rule == "cpl"` and
  `.severity == "warning"`.

---

## SC-04 — `fix` no-LLM glossary correction, timing immutable (passfail)

**Capability:** Deterministic no-LLM correction; glossary enforcement; cue-scoped
edits; `--json`; output + report artifacts.

**Setup / action:**
```bash
.venv/bin/subtitle-medic fix examples/sample.srt \
  --glossary examples/glossary.txt -o <tmp>/out.srt --report <tmp>/qa.md --json
```

**Success criterion (all must hold):**
- Exit code `== 0`.
- JSON `.used_model == false`, `.applied_count == 1`,
  `.structurally_valid == true`, `.ok == true`.
- `.edits[]` for cue 1 has `.changed == true` and its `.after[0]` contains both
  `"Kubernetes"` and `"GitHub"` and contains neither `"kuberntes"` nor the
  lowercase `"github"` (glossary canonicalization applied).
- `.telemetry.calls == 0` and `.telemetry.total_cost_usd == 0` (no model ran).
- The written `out.srt`, re-checked with `check --json`, has `.cue_count == 3`
  and `.has_errors == false`, and its cue-1 timing line is byte-identical to the
  input (`00:00:01,000 --> 00:00:09,000`) — i.e. only text changed.
- `qa.md` exists and contains the heading `# Caption correction report`.

---

## SC-05 — `fix --model` (hermetic ScriptedModel) cue-scoped edit preserves timing & count (rubric→passfail; threshold 1.0)

**Capability:** Model-mode cue-scoped correction; timing/index/count immutable by
construction; round-trip after edit; telemetry surfaced.

> Driven via the public library exactly like `examples/fix_demo.py`, because the
> CLI intentionally does not expose `scripted`/`mock` backends. This is the
> *only* scenario that exercises a model, and it is fully hermetic. It is listed
> as a rubric for bookkeeping but collapses to pass/fail — the threshold is
> **1.0 of 1.0**, i.e. every listed assertion must hold; no judgment is involved.

**Action (`.venv/bin/python`):** load `examples/sample.srt` and
`examples/glossary.txt`; build
`ScriptedModel(['@reply name=edit_cue\ntext = "We deployed to Kubernetes and the GitHub\\naction ran cleanly."\n@end', 'Done.', '@reply name=edit_cue\ntext = "Oops."\n@end', 'Done.'])`;
run `Corrector(scripted, glossary=gloss).correct(loads(text))`.

**Success criterion (score = fraction of assertions that hold; PASS iff == 1.0):**
- `result.used_model is True`.
- `result.applied_count == 2`.
- `result.structurally_valid is True`.
- `len(result.subtitles.cues) == 3` (cue count preserved).
- Cue 1 timing unchanged: `start_ms == 1000` and `end_ms == 9000`, even though
  its text was rewritten to two balanced lines.
- Cue 3 lines `== ["Oops."]`.
- `result.telemetry.as_dict()['calls'] > 0` (telemetry recorded the run).
- Round-trip: `[c.lines for c in loads(dumps(result.subtitles)).cues]` equals
  `[c.lines for c in result.subtitles.cues]`.

---

## SC-06 — Hard structural emit gate blocks a broken file (passfail)

**Capability:** Emit gate; exit-code-1 on `fix`; "nothing written" guarantee.

**Setup:** the out-of-order SRT from SC-02 (a pre-existing structural breakage
the corrector cannot repair, since it edits only text).
**Action:** `.venv/bin/subtitle-medic fix <tmp>/broken.srt -o <tmp>/o.srt --json`

**Success criterion (all must hold):**
- Exit code `== 1`.
- JSON `.structurally_valid == false`, `.ok == false`, and `.error` contains
  `"structural invariant"`.
- The output path `o.srt` was **not** created (no file on disk).

---

## SC-07 — WebVTT round-trip preserves header, NOTE, and cue settings (passfail)

**Capability:** Format parsing/auto-detect; round-trip fidelity; WebVTT
preservation.

**Action (`.venv/bin/python`):** `subs = loads(open('examples/sample.vtt').read())`;
`out = dumps(subs)`; `subs2 = loads(out)`.

**Success criterion (all must hold):**
- `detect_format(open('examples/sample.vtt').read())` is `CaptionFormat.VTT`.
- `subs.header == subs2.header` and the header contains both `"Kind: captions"`
  and a line starting with `"NOTE"`.
- `subs.cues[1].settings == "align:start position:10%"` and that exact string
  appears in `out`.
- Per-cue equality across the round-trip:
  `[(c.start_ms, c.end_ms, c.lines, c.settings) for c in subs.cues]` equals the
  same tuple list for `subs2.cues`.

---

## SC-08 — Clean WebVTT passes QA at defaults; no-LLM `fix` is a safe no-op (passfail)

**Capability:** Auto-detection on the clean VTT; no-LLM `fix` no-op pass; exit
codes.

**Action:**
```bash
.venv/bin/subtitle-medic check examples/sample.vtt --json   # baseline
.venv/bin/subtitle-medic fix   examples/sample.vtt --json   # no glossary, no model
```

**Success criterion (all must hold):**
- `check`: exit `0`, JSON `.ok == true`, `.has_errors == false`,
  `.cue_count == 3`, `.summary.total == 0`.
- `fix`: exit `0`, JSON `.applied_count == 0`, `.structurally_valid == true`,
  `.used_model == false`, `.ok == true`.

---

## SC-09 — Malformed caption file → usage error, single JSON object, exit 2 (passfail)

**Capability:** `ParseError` surfacing; exit-code-2 contract; `--json` failure
object.

**Setup:** a temp SRT whose timing line uses a single-dash `->` instead of `-->`:
```
1
00:00:01,000 -> 00:00:02,000
bad arrow
```
**Action:** `.venv/bin/subtitle-medic check <tmp>.srt --json`

**Success criterion (all must hold):**
- Exit code `== 2`.
- stdout is exactly one JSON object with `.ok == false` and a `.error` string
  that contains `"could not parse"` and `"-->"` (the parser names *where* it
  broke). No other (non-JSON) text on stdout.

---

## SC-10 — Missing file and bad `--model` both fail clean with exit 2 (passfail)

**Capability:** Usage-error handling for I/O and bad provider name; exit-code-2;
`--json` failure object.

**Action (two invocations):**
```bash
.venv/bin/subtitle-medic check /no/such/file.srt --json          # (a)
.venv/bin/subtitle-medic fix examples/sample.srt --model bogus --json  # (b)
```

**Success criterion (all must hold):**
- (a) Exit `2`; JSON `.ok == false`; `.error` contains `"no such file"`.
- (b) Exit `2`; JSON `.ok == false`; `.error` contains `"unknown --model"` and
  lists the valid choices (`anthropic`, `openai`, `ollama`). No partial output
  was written (no `-o` was given, so nothing to write; assert stdout is the
  single error object only).

---

## Coverage matrix

| Capability | Scenario(s) |
|---|---|
| Structural QA (`check`) | SC-01, SC-02 |
| Readability QA + thresholds | SC-01, SC-03 |
| Format parse / auto-detect | SC-07, SC-08, SC-09 |
| Round-trip fidelity / VTT preservation | SC-04 (SRT), SC-05, SC-07 |
| No-LLM deterministic correction | SC-04, SC-08 |
| Glossary enforcement | SC-04 |
| Model-mode cue-scoped correction | SC-05 |
| Hard structural emit gate | SC-06 |
| `--json` one-object contract | every scenario |
| Exit-code contract (0/1/2) | SC-01..SC-10 |
| Telemetry (zero-cost no-LLM, recorded model run) | SC-04, SC-05 |
| Markdown report artifact | SC-04 |
| Usage-error handling | SC-09, SC-10 |

**Edge / failure cases:** SC-02 (structural breakage), SC-06 (emit gate blocks a
broken file), SC-09 (malformed parse), SC-10 (missing file + bad provider).

All ten scenarios were dry-run against the current build; observed exit codes and
JSON field values match the criteria above.
