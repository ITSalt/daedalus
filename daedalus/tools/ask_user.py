"""The core's AskUser, registered as a host tool so that it carries a search hint like every other one.

The core ships the tool without a ``search_hint``; it is always loaded into the surface, but ToolSearch
still ranks it, and a Russian "уточни у меня" shares no word with its English description.
"""

from __future__ import annotations

from typing import ClassVar

from protocore.tools.ask_user import AskUserTool


class AskUser(AskUserTool):
    search_hint: ClassVar[str] = (
        "ask user question options choose clarify pause for answer ask me which one "
        "спросить спроси пользователя уточнить уточни выбрать выбери варианты вопрос спроси меня"
    )


TOOLS = [AskUser]

__all__ = ["TOOLS", "AskUser"]
