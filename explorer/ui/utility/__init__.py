"""
Utility tabs: tools that open in a tab next to the folder tabs.

    create(kind, panel, ctx, state)    -> a new utility tab
    KINDS                              -> {"search": SearchTab, "rename": RenameTab, "history": HistoryTab}

Adding a utility = write a UtilityTab subclass (see base.py) and add it to
``_REGISTRY`` below. The panel, the session and "reopen closed tab" handle
it from there: a saved tab state with {"utility": "<kind>"} is a utility tab.
"""
from __future__ import annotations

from typing import Dict, Optional

from .base import UtilityTab

_REGISTRY = {
    "search": ("explorer.ui.utility.search_tab", "SearchTab"),
    "rename": ("explorer.ui.utility.rename_tab", "RenameTab"),
    "history": ("explorer.ui.utility.history_tab", "HistoryTab"),
}


def load(kind: str) -> type:
    """The class for one kind, imported on first use (startup stays fast)."""
    import importlib
    module, name = _REGISTRY[kind]
    return getattr(importlib.import_module(module), name)


def kinds() -> Dict[str, type]:
    """{kind: class} for every registered utility."""
    return {kind: load(kind) for kind in _REGISTRY}


def create(kind: str, panel, ctx, state: Optional[dict] = None) -> UtilityTab:
    if kind not in _REGISTRY:
        raise KeyError(f"Unknown utility: {kind}")
    return load(kind)(panel, ctx, state)


def is_utility_state(state: Optional[dict]) -> bool:
    return bool(state) and state.get("utility") in _REGISTRY


__all__ = ["UtilityTab", "create", "kinds", "is_utility_state"]
