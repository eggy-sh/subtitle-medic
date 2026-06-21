#!/usr/bin/env python3
"""Deterministic caption QA, end to end and fully offline.

Parses the bundled ``sample.srt`` (which deliberately breaks several rules), runs
the rule engine, and prints both the human Markdown report and the one-object
JSON the ``--json`` CLI mode emits. No model, no network, no API key.

Run it::

    python examples/check_demo.py
"""

from __future__ import annotations

import json
from pathlib import Path

from subtitle_medic.parser import loads
from subtitle_medic.report import check_report_json, check_report_markdown
from subtitle_medic.rules import CheckConfig, check

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "sample.srt"


def main() -> None:
    text = SAMPLE.read_text(encoding="utf-8")
    subs = loads(text)

    # Studio-ish defaults; tweak any threshold here just like the CLI flags.
    config = CheckConfig(max_cps=17.0, max_cpl=42, max_lines=2)
    report = check(subs, config)

    print("=== Markdown report (the --report artifact) ===\n")
    print(check_report_markdown(report, source=str(SAMPLE)))

    print("\n=== JSON (what `subtitle-medic check --json` prints) ===\n")
    print(json.dumps(check_report_json(report, source=str(SAMPLE)), indent=2))

    print(f"\nflagged cue indices: {report.flagged_indices()}")
    print(f"structural errors present: {report.has_errors}")


if __name__ == "__main__":
    main()
