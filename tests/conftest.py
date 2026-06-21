"""Shared, hermetic pytest fixtures for the subtitle-medic test suite.

No network and no live LLM: correction tests drive the corrector with replykit's
``ScriptedModel`` / ``MockModel``. Fixtures here provide canonical sample caption
text (valid and intentionally-broken), a sample glossary, and parsed
:class:`~subtitle_medic.model.Subtitles` documents so both SWE-Core (library) and
SWE-CLI (integration) suites share one source of truth.
"""

from __future__ import annotations

import pytest

# --- Raw caption text samples ---------------------------------------------

#: A small, fully valid SRT: contiguous indices, monotonic non-overlapping
#: timing, two short lines per cue. The round-trip and clean-check anchor.
VALID_SRT = """\
1
00:00:01,000 --> 00:00:03,000
Hello world.

2
00:00:03,500 --> 00:00:06,000
This is a second cue
spanning two lines.

3
00:00:06,200 --> 00:00:08,000
Final cue here.
"""

#: The same content as WebVTT, including a header NOTE and one cue setting, to
#: exercise VTT-specific round-trip preservation.
VALID_VTT = """\
WEBVTT
Kind: captions

NOTE This is a sample file.

1
00:00:01.000 --> 00:00:03.000
Hello world.

2
00:00:03.500 --> 00:00:06.000 align:start position:10%
This is a second cue
spanning two lines.

3
00:00:06.200 --> 00:00:08.000
Final cue here.
"""

#: An SRT that breaks multiple rules: non-contiguous index (1,3), an overlap
#: (cue 3 starts before cue 1 ends... it's deliberately tangled), a too-short
#: duration, an over-long line (CPS/CPL), and a glossary-correctable brand typo.
BROKEN_SRT = """\
1
00:00:01,000 --> 00:00:09,000
We deployed to kuberntes and the github action ran a realy long line that exceeds the limit.

3
00:00:08,500 --> 00:00:08,600
oops

"""

#: A sample glossary covering a bare canonical term and an explicit mapping.
GLOSSARY_TEXT = """\
# brand + product terms
GitHub
kuberntes => Kubernetes
Kubernetes
"""


@pytest.fixture
def valid_srt() -> str:
    return VALID_SRT


@pytest.fixture
def valid_vtt() -> str:
    return VALID_VTT


@pytest.fixture
def broken_srt() -> str:
    return BROKEN_SRT


@pytest.fixture
def glossary_text() -> str:
    return GLOSSARY_TEXT


@pytest.fixture
def tmp_srt_file(tmp_path, valid_srt):
    """A path to a valid .srt file written under pytest's tmp_path."""
    p = tmp_path / "sample.srt"
    p.write_text(valid_srt, encoding="utf-8")
    return p


@pytest.fixture
def tmp_broken_srt_file(tmp_path, broken_srt):
    """A path to an intentionally-broken .srt file."""
    p = tmp_path / "broken.srt"
    p.write_text(broken_srt, encoding="utf-8")
    return p


@pytest.fixture
def tmp_glossary_file(tmp_path, glossary_text):
    """A path to a sample glossary file."""
    p = tmp_path / "glossary.txt"
    p.write_text(glossary_text, encoding="utf-8")
    return p
