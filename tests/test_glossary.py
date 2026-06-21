"""Tests for glossary parsing and lookup."""

from __future__ import annotations

from subtitle_medic.glossary import (
    EMPTY_GLOSSARY,
    Glossary,
    load_glossary,
    parse_glossary,
)


def test_parse_handles_bare_term_mapping_comments_blanks(glossary_text):
    gl = parse_glossary(glossary_text)
    # Bare terms: GitHub, Kubernetes. Mapping: kuberntes => Kubernetes.
    assert "GitHub" in gl.terms
    assert "Kubernetes" in gl.terms
    assert gl.mappings["kuberntes"] == "Kubernetes"


def test_comments_and_blanks_ignored():
    text = "# a comment\n\n   \nFoo\n# another\nbar => Baz\n"
    gl = parse_glossary(text)
    assert gl.terms == ("Foo",)
    assert gl.mappings == {"bar": "Baz"}


def test_canonical_for_case_insensitive_terms():
    gl = parse_glossary("GitHub\nKubernetes\n")
    assert gl.canonical_for("GITHUB") == "GitHub"
    assert gl.canonical_for("github") == "GitHub"
    assert gl.canonical_for("kubernetes") == "Kubernetes"


def test_canonical_for_case_insensitive_mappings():
    gl = parse_glossary("kuberntes => Kubernetes\n")
    assert gl.canonical_for("kuberntes") == "Kubernetes"
    assert gl.canonical_for("KUBERNTES") == "Kubernetes"


def test_canonical_for_returns_none_for_ungoverned():
    gl = parse_glossary("GitHub\n")
    assert gl.canonical_for("python") is None


def test_prompt_block_empty_for_empty_glossary():
    assert EMPTY_GLOSSARY.prompt_block() == ""
    assert parse_glossary("").prompt_block() == ""


def test_prompt_block_contains_all_terms(glossary_text):
    gl = parse_glossary(glossary_text)
    block = gl.prompt_block()
    assert block != ""
    assert "GitHub" in block
    assert "Kubernetes" in block
    # The mapping is represented too.
    assert "kuberntes" in block


def test_len_and_bool():
    gl = parse_glossary("GitHub\nFoo\n")
    assert len(gl) == 2
    assert bool(gl) is True
    assert bool(EMPTY_GLOSSARY) is False
    assert len(EMPTY_GLOSSARY) == 0


def test_bool_true_when_only_mappings():
    gl = parse_glossary("bad => Good\n")
    assert len(gl) == 0
    assert bool(gl) is True


def test_load_glossary_from_file(tmp_glossary_file):
    gl = load_glossary(str(tmp_glossary_file))
    assert "GitHub" in gl.terms
    assert gl.mappings["kuberntes"] == "Kubernetes"


def test_load_empty_file_yields_empty_glossary(tmp_path):
    p = tmp_path / "empty.txt"
    p.write_text("", encoding="utf-8")
    gl = load_glossary(str(p))
    assert bool(gl) is False
    assert gl == Glossary()


def test_parse_never_raises_on_ordinary_content():
    # Weird-but-ordinary content must not crash.
    weird = "=> only right side\nleft only =>\n   => \nGood\n"
    gl = parse_glossary(weird)
    assert "Good" in gl.terms


def test_empty_glossary_singleton_is_empty():
    assert EMPTY_GLOSSARY.terms == ()
    assert EMPTY_GLOSSARY.mappings == {}
