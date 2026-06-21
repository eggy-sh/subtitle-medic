"""Glossary loading: the canonical spellings the corrector must enforce.

A glossary is a newline-delimited list of preferred terms — proper nouns, brand
names, product names, domain jargon — that captions should spell exactly. Two
line shapes are supported:

* ``Term`` — a canonical term with no explicit wrong form. The corrector treats
  it as the authoritative spelling and fixes near-miss variants.
* ``wrong => Right`` — an explicit mapping from a known bad form to its canonical
  form (e.g. ``kubernetes => Kubernetes``).

Blank lines and ``#`` comment lines are ignored. The result is a frozen
:class:`Glossary` the corrector injects into the prompt and uses to validate that
an edit did not corrupt a known term.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Glossary:
    """A set of canonical terms plus explicit wrong-to-right mappings."""

    terms: tuple[str, ...] = ()
    # Lower-cased wrong form -> canonical replacement.
    mappings: dict[str, str] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.terms)

    def __bool__(self) -> bool:
        return bool(self.terms) or bool(self.mappings)

    def prompt_block(self) -> str:
        """Render the glossary as a compact, token-minimal prompt fragment.

        Empty string when the glossary is empty, so an absent glossary adds no
        tokens to the correction prompt.
        """
        if not self:
            return ""
        lines = ["Glossary (use these exact spellings):"]
        for term in self.terms:
            lines.append(f"- {term}")
        for wrong, right in self.mappings.items():
            lines.append(f"- {wrong} -> {right}")
        return "\n".join(lines)

    def canonical_for(self, token: str) -> str | None:
        """Return the canonical spelling for a token, or ``None`` if not governed.

        Matches case-insensitively against both ``terms`` and the keys of
        ``mappings``; used to verify a corrected cue uses approved spellings.
        """
        lowered = token.lower()
        for term in self.terms:
            if term.lower() == lowered:
                return term
        for wrong, right in self.mappings.items():
            if wrong.lower() == lowered:
                return right
        return None


def parse_glossary(text: str) -> Glossary:
    """Parse glossary text (one term or ``wrong => Right`` per line) into a Glossary.

    Ignores blank lines and ``#`` comments. Never raises on ordinary content.
    """
    terms: list[str] = []
    seen: set[str] = set()
    mappings: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            wrong, _, right = line.partition("=>")
            wrong = wrong.strip()
            right = right.strip()
            if wrong and right:
                mappings[wrong] = right
            continue
        if line.lower() not in seen:
            seen.add(line.lower())
            terms.append(line)
    return Glossary(terms=tuple(terms), mappings=mappings)


def load_glossary(path: str) -> Glossary:
    """Read and parse a glossary file from ``path`` (UTF-8). Empty file -> empty Glossary."""
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    return parse_glossary(text)


#: The empty glossary, used when no ``--glossary`` is supplied.
EMPTY_GLOSSARY = Glossary()
