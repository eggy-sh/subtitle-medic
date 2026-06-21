# subtitle-medic — acceptance evidence

Hermetic, reproducible acceptance run. No network and no live LLM: CLI scenarios run the deterministic no-LLM path; SC-05 uses replykit's `ScriptedModel`. All scenarios executed under identical conditions by `acceptance/test_acceptance.py`.

- Repository: `/Users/ehernand/personal_projects/postpro-kit/subtitle-medic`
- Interpreter: `/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/python`
- CLI under test: `/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic`
- Hermetic env: provider API keys (`ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`OLLAMA_HOST`) scrubbed.

## Result: 10/10 scenarios PASS

| Scenario | Verdict | Capability |
| --- | --- | --- |
| SC-01 | PASS | Structural + readability QA; --json contract; exit codes |
| SC-02 | PASS | Structural invariants; exit-code-1 contract |
| SC-03 | PASS | Configurable readability thresholds; flag plumbing |
| SC-04 | PASS | Deterministic no-LLM correction; glossary; artifacts; telemetry |
| SC-05 | PASS | Model-mode cue-scoped correction via hermetic ScriptedModel |
| SC-06 | PASS | Hard structural emit gate; exit-1 on fix; nothing written |
| SC-07 | PASS | Format auto-detect; round-trip fidelity; VTT preservation |
| SC-08 | PASS | Auto-detection on clean VTT; no-LLM fix no-op pass; exit codes |
| SC-09 | PASS | ParseError surfacing; exit-code-2; --json failure object |
| SC-10 | PASS | Usage errors (I/O + bad provider); exit-code-2; failure object |

### SC-01 — PASS

**Capability:** Structural + readability QA; --json contract; exit codes

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.srt --json
```

**Captured output / artifact summary:**

exit=0; has_errors=false; cue_count=3; warnings=3; flagged=[1,3]; violations=[('max_duration', 1, 8000, 7000), ('cpl', 1, 99, 42), ('min_duration', 3, 300, 700)]; all severity 'warning'.

### SC-02 — PASS

**Capability:** Structural invariants; exit-code-1 contract

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmpra2xbg23/out_of_order.srt --json  (cue 2 starts before cue 1)
```

**Captured output / artifact summary:**

exit=1; has_errors=true; ok=false; errors=2; timing_order(cue 2, observed 1000, limit 5000, error); timing_overlap(cue 2, observed 1000, limit 8000, error).

### SC-03 — PASS

**Capability:** Configurable readability thresholds; flag plumbing

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.vtt --max-cpl 10 --json
```

**Captured output / artifact summary:**

exit=0; ok=false; has_errors=false; warnings=3; errors=0; all 3 violations rule=='cpl' severity=='warning'.

### SC-04 — PASS

**Capability:** Deterministic no-LLM correction; glossary; artifacts; telemetry

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic fix /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.srt --glossary /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/glossary.txt -o /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmpethz2fvw/out.srt --report /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmpethz2fvw/qa.md --json
```

**Captured output / artifact summary:**

exit=0; used_model=false; applied_count=1; structurally_valid=true; ok=true; cue1.after[0]='We deployed to Kubernetes and the GitHub action ran a realy long line that exceeds the studio limit.' (has Kubernetes+GitHub, no 'kuberntes'/'github'); telemetry.calls=0, total_cost_usd=0; recheck out.srt cue_count=3 has_errors=false; timing line '00:00:01,000 --> 00:00:09,000' preserved byte-identical; qa.md contains '# Caption correction report'.

### SC-05 — PASS

**Capability:** Model-mode cue-scoped correction via hermetic ScriptedModel

**Command / inputs:**

```
library: Corrector(ScriptedModel([edit_cue cue1, 'Done.', edit_cue cue3, 'Done.']), glossary).correct(loads(open(/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.srt).read()))
```

**Captured output / artifact summary:**

rubric score 9/9 == 1.0; used_model=True; applied_count=2; structurally_valid=True; len(cues)=3; cue1 timing (1000,9000) unchanged; cue3 lines=['Oops.']; telemetry.calls=4>0; round-trip lines equal.

### SC-06 — PASS

**Capability:** Hard structural emit gate; exit-1 on fix; nothing written

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic fix /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmp_mx_cm5l/broken.srt -o /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmp_mx_cm5l/o.srt --json  (out-of-order SRT from SC-02)
```

**Captured output / artifact summary:**

exit=1; structurally_valid=false; ok=false; error contains 'structural invariant' ('correction would break a structural invariant; nothing written'); output o.srt NOT created.

### SC-07 — PASS

**Capability:** Format auto-detect; round-trip fidelity; VTT preservation

**Command / inputs:**

```
library: subs=loads(open(sample.vtt).read()); out=dumps(subs); subs2=loads(out)
```

**Captured output / artifact summary:**

detect_format==CaptionFormat.VTT; header equal across round-trip and contains 'Kind: captions' + a 'NOTE' line; cues[1].settings=='align:start position:10%' appears in out; per-cue (start_ms,end_ms,lines,settings) tuples equal.

### SC-08 — PASS

**Capability:** Auto-detection on clean VTT; no-LLM fix no-op pass; exit codes

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.vtt --json  AND  /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic fix /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.vtt --json
```

**Captured output / artifact summary:**

check: exit=0, ok=true, has_errors=false, cue_count=3, total=0. fix: exit=0, applied_count=0, structurally_valid=true, used_model=false, ok=true.

### SC-09 — PASS

**Capability:** ParseError surfacing; exit-code-2; --json failure object

**Command / inputs:**

```
/Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmpqh11dpm_/bad.srt --json  (timing uses '->' not '-->')
```

**Captured output / artifact summary:**

exit=2; single JSON object on stdout; ok=false; error contains 'could not parse' and '-->' ("could not parse /var/folders/pr/314hjw5519v__yd944qzp2q0000q5d/T/tmpqh11dpm_/bad.srt: cue 1: missing '-->' in timing line: '00:00:01,000 -> 00:00:02,000'").

### SC-10 — PASS

**Capability:** Usage errors (I/O + bad provider); exit-code-2; failure object

**Command / inputs:**

```
(a) /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic check /no/such/file.srt --json   (b) /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/.venv/bin/subtitle-medic fix /Users/ehernand/personal_projects/postpro-kit/subtitle-medic/examples/sample.srt --model bogus --json
```

**Captured output / artifact summary:**

(a) exit=2; ok=false; error contains 'no such file'. (b) exit=2; ok=false; error contains 'unknown --model' and lists anthropic/openai/ollama; stdout is the single error object only.
