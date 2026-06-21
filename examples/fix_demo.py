#!/usr/bin/env python3
"""Cue-scoped correction, end to end and fully offline.

Drives the corrector two ways, both hermetic (no network, no API key):

1. **No-LLM mode** (``Corrector(None, ...)``) — the safe, deterministic pass that
   applies glossary spellings and whitespace fixes to *flagged* cues only.
2. **Model mode** with a :class:`~replykit.ScriptedModel` standing in for a real
   provider. The scripted model "talks" the replykit ``@reply`` protocol: for the
   one flagged cue it calls the single ``edit_cue`` tool with corrected text, then
   gives a final plain-text turn. Because ``edit_cue`` has no timing argument, the
   model structurally cannot desync the captions — the corrector copies timing and
   index through verbatim and re-validates the structural invariants after the edit.

Run it::

    python examples/fix_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from replykit import ScriptedModel

from subtitle_medic.corrector import Corrector
from subtitle_medic.glossary import load_glossary
from subtitle_medic.parser import dumps, loads
from subtitle_medic.report import correction_report_json, correction_report_markdown

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample.srt"
GLOSSARY = HERE / "glossary.txt"


def _show(title: str, result) -> None:
    print(f"\n=== {title} ===")
    print(
        f"used_model={result.used_model}  applied={result.applied_count}  "
        f"structurally_valid={result.structurally_valid}"
    )
    for edit in result.edits:
        if edit.changed:
            print(f"  cue {edit.cue_index}: {edit.before!r} -> {edit.after!r}")
        else:
            print(f"  cue {edit.cue_index}: (no change) {edit.reason}")


def main() -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    glossary = load_glossary(str(GLOSSARY))

    # 1. No-LLM mode: deterministic glossary/whitespace fixes on flagged cues.
    no_llm = Corrector(None, glossary=glossary).correct(loads(text))
    _show("No-LLM mode (deterministic)", no_llm)

    # 2. Model mode with a hermetic ScriptedModel. The corrector runs one agent
    #    pass per *flagged* cue (here cues 1 and 3). For each, the model replies
    #    with a single edit_cue tool call carrying the corrected text, then a final
    #    plain-text turn. The corrector applies each edit via Cue.with_lines, so
    #    timing/index are untouched and the structural invariants are re-validated.
    # '\\n' is a literal backslash-n in the protocol text; the edit_cue tool turns
    # it back into a hard line break, so this cue ends up as two balanced lines.
    cue1_fixed = "We deployed to Kubernetes and the GitHub\\naction ran cleanly."
    cue3_fixed = "Oops."
    scripted = ScriptedModel(
        [
            f'@reply name=edit_cue\ntext = "{cue1_fixed}"\n@end',
            "Done.",
            f'@reply name=edit_cue\ntext = "{cue3_fixed}"\n@end',
            "Done.",
        ]
    )
    with_model = Corrector(scripted, glossary=glossary).correct(loads(text))
    _show("Model mode (ScriptedModel — hermetic)", with_model)

    # The hard emit gate: only ever serialize a structurally-valid document.
    assert with_model.structurally_valid

    print("\n=== Corrected SRT (model mode) ===\n")
    print(dumps(with_model.subtitles))

    print("=== JSON (what `subtitle-medic fix --json` prints) ===\n")
    print(json.dumps(correction_report_json(with_model, source=str(SAMPLE)), indent=2))

    print("\n=== Markdown report (the --report artifact) ===\n")
    print(correction_report_markdown(with_model, source=str(SAMPLE)))


if __name__ == "__main__":
    main()
