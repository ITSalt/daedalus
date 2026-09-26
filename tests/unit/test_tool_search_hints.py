"""Every tool the host registers carries a search hint in English and Russian.

The core's tool retriever indexes a tool's name, its ``search_hint``, its description and its
parameters. Descriptions are English, and the operator mostly writes Russian: a tool without a hint
is found by a Russian message only through the core's generic lexicon, and colloquial words
("скинь", "глянь", "напоминалка") not at all. This test is what stops a new tool from shipping without one.
"""

from __future__ import annotations

import re

import pytest
from protocore.contracts.tools import Tool
from protocore.tools.memory import build_memory_tools

from daedalus.tools import discover_tools
from daedalus.tools.dispatcher import build as build_dispatcher_tools

LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]*")
CYRILLIC_WORD = re.compile(r"[А-Яа-яЁё][А-Яа-яЁё0-9-]*")

# The convention is roughly ten English and a dozen Russian words. The floor catches a hint that is a
# token gesture; the ceiling catches a pasted paragraph, which drowns the words that matter because
# the retriever normalises a field by its length.
MIN_LATIN_WORDS = 5
MIN_CYRILLIC_WORDS = 8
MAX_WORDS = 40
MAX_LENGTH = 300


def _host_tools() -> list[Tool]:
    return [*discover_tools(), *build_dispatcher_tools()]


def _hint(tool: Tool) -> str:
    # Read the way the core reads it: an attribute of the registered object, not of the definition.
    return getattr(tool, "search_hint", "")


@pytest.mark.parametrize("tool", _host_tools(), ids=lambda tool: tool.name)
def test_every_host_tool_has_an_english_and_russian_search_hint(tool: Tool) -> None:
    hint = _hint(tool)
    assert isinstance(hint, str) and hint.strip(), f"{tool.name} has no search_hint"
    words = hint.split()
    latin = [word for word in words if LATIN_WORD.fullmatch(word)]
    cyrillic = [word for word in words if CYRILLIC_WORD.fullmatch(word)]
    assert len(latin) >= MIN_LATIN_WORDS, f"{tool.name}: {len(latin)} English words in its search_hint"
    assert len(cyrillic) >= MIN_CYRILLIC_WORDS, f"{tool.name}: {len(cyrillic)} Russian words in its search_hint"
    assert len(words) <= MAX_WORDS and len(hint) <= MAX_LENGTH, f"{tool.name}: search_hint too long ({len(words)} words)"


def test_the_hint_never_reaches_the_schema_the_model_sees() -> None:
    for tool in _host_tools():
        assert _hint(tool) not in tool.definition.description


def test_the_core_memory_tools_carry_both_languages_too() -> None:
    # Registered by the host, written by the core: only the presence of both scripts is ours to check.
    for tool in build_memory_tools(object()):  # type: ignore[arg-type]
        hint = _hint(tool)
        assert LATIN_WORD.search(hint) and CYRILLIC_WORD.search(hint), tool.name
