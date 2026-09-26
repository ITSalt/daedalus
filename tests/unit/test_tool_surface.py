"""What the model is shown of the tools: every host tool while it fits, groups held back when it does not."""

from __future__ import annotations

from daedalus.tools import TOOL_GROUPS, discover_tools


def test_every_group_of_host_tools_is_declared_and_the_everyday_tools_are_in_none() -> None:
    grouped = {tool.name: getattr(tool, "tool_group", "") for tool in discover_tools()}
    assert {group for group in grouped.values() if group} == set(TOOL_GROUPS)
    # Never held back: what every task needs, and what the models failed to search for when it was.
    for name in ("Read", "Write", "Edit", "MultiEdit", "Find", "Search", "Exec", "WebSearch", "WebFetch", "AskUser", "Skill", "Notify", "HistorySearch"):
        assert not grouped[name], name
    # Held back only by name in a catalogue line that says what the group is for.
    assert grouped["ServiceStart"] == "services" and "ServiceStart" in TOOL_GROUPS["services"]
    assert grouped["IntentCreate"] == "scheduling" and "IntentCreate" in TOOL_GROUPS["scheduling"]
