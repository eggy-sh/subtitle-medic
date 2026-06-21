"""Cue-scoped LLM correction built on the replykit Agent.

For each flagged cue, the corrector asks a model to propose a **text-only** edit
that fixes typos, enforces glossary spellings (proper nouns / brand terms), and
rebalances lines — *without* changing the cue's timing, index, or count. The model
talks through replykit's ``@reply`` tool protocol: it calls a single registered
``edit_cue`` tool whose arguments are the new text lines, and the corrector
applies that edit via :meth:`~subtitle_medic.model.Cue.with_lines`, which
structurally cannot touch timing.

Safety rails (enforced in code, not trusted to the model):

* **Cue count and timing are immutable.** Only ``lines`` are replaced; the index
  and start/end milliseconds are copied through verbatim.
* **Re-validation gate.** After every cue edit, the whole document is re-checked
  with :func:`~subtitle_medic.rules.structural_violations`. If an edit would
  introduce a structural breakage (it cannot, given the model above, but the gate
  is defensive), the edit is rejected and the original cue is kept.
* **Graceful no-LLM mode.** When ``model is None`` the corrector performs only the
  deterministic, glossary-driven mappings it can apply safely on its own and
  records that no model ran — so ``fix`` degrades to a pure-rules pass when no
  provider is configured.

The corrector is engine-agnostic: it accepts any object satisfying replykit's
:class:`~replykit.Model` protocol, so tests drive it with ``ScriptedModel`` /
``MockModel`` and it runs hermetically with no network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from replykit import (
    Agent,
    Model,
    Telemetry,
    ToolRegistry,
)

from .glossary import EMPTY_GLOSSARY, Glossary
from .model import Cue, Subtitles
from .rules import CheckConfig, CheckReport, check, structural_violations

#: The single tool the correction model may call. Its only argument is the new
#: text; timing and index are never parameters, so the model structurally cannot
#: alter them.
EDIT_TOOL_NAME = "edit_cue"


def _structural_signatures(violations: list) -> set[tuple[Any, int]]:
    """A position-aware signature set of structural violations for gate comparison."""
    return {(v.rule, v.cue_index) for v in violations}


@dataclass(frozen=True)
class CueEdit:
    """One applied (or rejected) cue correction."""

    cue_index: int
    before: list[str]
    after: list[str]
    applied: bool
    reason: str = ""
    repair_attempts: int = 0

    @property
    def changed(self) -> bool:
        """True when the edit was applied and actually altered the text."""
        return self.applied and self.before != self.after

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable view of the edit."""
        return {
            "cue_index": self.cue_index,
            "before": list(self.before),
            "after": list(self.after),
            "applied": self.applied,
            "changed": self.changed,
            "reason": self.reason,
            "repair_attempts": self.repair_attempts,
        }


@dataclass
class CorrectionResult:
    """The outcome of correcting a document."""

    subtitles: Subtitles
    edits: list[CueEdit] = field(default_factory=list)
    pre_report: CheckReport | None = None
    post_report: CheckReport | None = None
    telemetry: Telemetry | None = None
    used_model: bool = False

    @property
    def applied_count(self) -> int:
        """How many edits were actually applied (text changed)."""
        return sum(1 for edit in self.edits if edit.changed)

    @property
    def structurally_valid(self) -> bool:
        """True when the corrected document has no structural ERROR violations.

        This is the hard gate the CLI checks before writing output: never emit a
        file whose structural invariants are broken.
        """
        return not structural_violations(self.subtitles)

    def as_dict(self) -> dict[str, Any]:
        """A JSON-serializable summary of the correction run."""
        return {
            "used_model": self.used_model,
            "applied_count": self.applied_count,
            "structurally_valid": self.structurally_valid,
            "subtitles": self.subtitles.as_dict(),
            "edits": [edit.as_dict() for edit in self.edits],
            "pre_report": self.pre_report.as_dict() if self.pre_report is not None else None,
            "post_report": self.post_report.as_dict() if self.post_report is not None else None,
            "telemetry": self.telemetry.as_dict() if self.telemetry is not None else None,
        }


class Corrector:
    """Drives cue-scoped corrections over a flagged caption document.

    Construct with an optional replykit ``model`` (``None`` => no-LLM mode), a
    :class:`~subtitle_medic.glossary.Glossary`, and a
    :class:`~subtitle_medic.rules.CheckConfig`. :meth:`correct` runs the full
    pass; the per-cue plumbing is exposed for testing.
    """

    def __init__(
        self,
        model: Model | None = None,
        *,
        glossary: Glossary = EMPTY_GLOSSARY,
        config: CheckConfig | None = None,
        telemetry: Telemetry | None = None,
        max_attempts: int = 3,
    ) -> None:
        self.model = model
        self.glossary = glossary
        self.config = config or CheckConfig()
        self.telemetry = telemetry if telemetry is not None else Telemetry()
        self.max_attempts = max_attempts

    def correct(self, subs: Subtitles, report: CheckReport | None = None) -> CorrectionResult:
        """Correct every flagged cue and return a validated :class:`CorrectionResult`.

        Runs :func:`~subtitle_medic.rules.check` first (unless ``report`` is
        supplied), edits only the flagged cues, re-validates structural invariants
        after each edit, and guarantees the returned document has the same cue
        count and per-cue timing as the input. In no-LLM mode only safe
        glossary/whitespace normalizations are applied.
        """
        pre_report = report if report is not None else check(subs, self.config)
        flagged = set(pre_report.flagged_indices())

        # Baseline of pre-existing structural breakage. A text-only edit can never
        # affect structural rules (index/timing), so the re-validation gate rejects
        # only edits that would *add* a structural violation, not pre-existing ones
        # the corrector cannot repair (it edits text, never timing/index/count).
        baseline = _structural_signatures(structural_violations(subs))

        # Work on a mutable copy of the cue list; timing/index/count are never
        # touched because we only ever replace ``lines`` via ``with_lines``.
        cues: list[Cue] = list(subs.cues)
        edits: list[CueEdit] = []

        for position, cue in enumerate(cues):
            if cue.index not in flagged:
                continue
            before = list(cue.lines)
            after, reason, attempts = self._propose_lines(before)
            if after == before:
                edits.append(
                    CueEdit(
                        cue_index=cue.index,
                        before=before,
                        after=before,
                        applied=False,
                        reason=reason or "no change proposed",
                        repair_attempts=attempts,
                    )
                )
                continue

            candidate = cue.with_lines(after)
            trial = list(cues)
            trial[position] = candidate
            # Re-validation gate: never accept an edit that *introduces* a new
            # structural breakage. (By construction it cannot, given only ``lines``
            # change, but the gate is defensive.)
            after_sigs = _structural_signatures(structural_violations(subs.with_cues(trial)))
            if after_sigs - baseline:
                edits.append(
                    CueEdit(
                        cue_index=cue.index,
                        before=before,
                        after=before,
                        applied=False,
                        reason="edit rejected: would break a structural invariant",
                        repair_attempts=attempts,
                    )
                )
                continue

            cues[position] = candidate
            edits.append(
                CueEdit(
                    cue_index=cue.index,
                    before=before,
                    after=after,
                    applied=True,
                    reason=reason,
                    repair_attempts=attempts,
                )
            )

        corrected = subs.with_cues(cues)
        post_report = check(corrected, self.config)
        return CorrectionResult(
            subtitles=corrected,
            edits=edits,
            pre_report=pre_report,
            post_report=post_report,
            telemetry=self.telemetry,
            used_model=self.model is not None,
        )

    def _propose_lines(self, lines: list[str]) -> tuple[list[str], str, int]:
        """Return (new_lines, reason, repair_attempts) for one flagged cue."""
        if self.model is None:
            new_lines = self._safe_normalize(lines)
            reason = "deterministic glossary/whitespace fix" if new_lines != lines else ""
            return new_lines, reason, 0
        new_lines = self.correct_cue_text(lines)
        reason = "model edit" if new_lines != lines else ""
        return new_lines, reason, 0

    def _safe_normalize(self, lines: list[str]) -> list[str]:
        """Deterministic, model-free fixes: glossary mappings + whitespace.

        Only token-for-token substitutions of governed terms and whitespace
        collapsing are applied — never anything that could change cue identity.
        """
        out: list[str] = []
        for line in lines:
            # ``str.split()`` with no args collapses runs of whitespace and trims
            # the ends — a safe normalization that never changes word identity.
            tokens = line.split()
            fixed = [self._apply_glossary_token(token) for token in tokens]
            out.append(" ".join(fixed))
        return out

    def _apply_glossary_token(self, token: str) -> str:
        """Map one whitespace-delimited token through the glossary, keeping punctuation."""
        if not token:
            return token
        # Separate leading/trailing punctuation so "github." maps cleanly.
        start = 0
        end = len(token)
        while start < end and not token[start].isalnum():
            start += 1
        while end > start and not token[end - 1].isalnum():
            end -= 1
        prefix, core, suffix = token[:start], token[start:end], token[end:]
        if not core:
            return token
        canonical = self.glossary.canonical_for(core)
        if canonical is not None and canonical != core:
            return f"{prefix}{canonical}{suffix}"
        return token

    def correct_cue_text(self, lines: list[str]) -> list[str]:
        """Propose corrected text lines for a single cue via the model.

        Builds a cue-scoped replykit :class:`~replykit.Agent` run with a single
        ``edit_cue`` tool and the glossary injected once, parses the model's
        chosen lines, and returns them. Raises if no model is configured — callers
        gate on :attr:`model` first. Timing is never part of this call.
        """
        if self.model is None:
            raise RuntimeError("correct_cue_text requires a configured model")

        registry, holder = self._build_registry()
        task = self._build_task(lines)
        agent = Agent(
            self.model,
            registry,
            telemetry=self.telemetry,
            max_attempts=self.max_attempts,
            max_steps=self.max_attempts + 1,
        )
        agent.run(task)
        if holder["lines"] is not None:
            return holder["lines"]
        # The model never produced a usable edit_cue call; keep the original text.
        return list(lines)

    def _build_task(self, lines: list[str]) -> str:
        original = "\n".join(lines)
        parts = [
            "Fix typos and spelling in this subtitle cue. Keep the meaning. "
            "Do not add or remove lines unnecessarily. Call edit_cue with the "
            "corrected text, using '\\n' between lines.",
        ]
        block = self.glossary.prompt_block()
        if block:
            parts.append(block)
        parts.append(f"Cue text:\n{original}")
        return "\n\n".join(parts)

    def _build_registry(self) -> tuple[ToolRegistry, dict[str, Any]]:
        """Build the one-tool replykit :class:`~replykit.ToolRegistry` for a cue.

        The single ``edit_cue`` tool accepts the corrected text and is the only
        action the model can take, so the model can never alter timing or index.
        Returns the registry plus a holder dict the tool writes the chosen lines
        into, so the corrector can read them after the agent run.
        """
        holder: dict[str, Any] = {"lines": None}
        registry = ToolRegistry()

        @registry.register(name=EDIT_TOOL_NAME)
        def edit_cue(text: str) -> str:
            "Replace the cue's text with the corrected version (lines split on \\n)."
            holder["lines"] = text.replace("\\n", "\n").split("\n")
            return "ok"

        return registry, holder
