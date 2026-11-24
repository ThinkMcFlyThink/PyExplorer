"""
A tiny publish/subscribe "event bus".

Why: components should not need to know about each other. When the user
switches the theme, the theme code does NOT loop over every view calling
``view.redraw()``. It just announces ``Topics.THEME_CHANGED`` and whoever
cares (views, sidebar, terminal...) reacts. Adding a new component never
requires editing the code that sends the event.

All publishing happens on the UI (main) thread. Background threads must hand
their results to the UI thread first (see ``core.tasks.UIDispatcher``).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Callable, Dict, List

log = logging.getLogger(__name__)


class Topics:
    """Names of all events used in the app. Keeping them in one place
    makes it easy to see what can be listened to."""
    SETTINGS_CHANGED = "settings.changed"            # key=str, value=any
    THEME_CHANGED = "theme.changed"                  # name=str
    VIEW_MODE_CHANGED = "view.mode_changed"          # mode=str
    ZOOM_CHANGED = "view.zoom_changed"               # mode=str, level=int
    SHOW_HIDDEN_CHANGED = "view.show_hidden_changed"  # value=bool
    SORT_CHANGED = "view.sort_changed"               # key=str, reverse=bool
    ACTIVE_LOCATION_CHANGED = "location.active_changed"  # path=str, tab=obj
    SELECTION_CHANGED = "selection.changed"          # tab=obj
    FOLDER_COLORS_CHANGED = "folders.colors_changed"  # path=str|None
    CATEGORIES_CHANGED = "sidebar.categories_changed"
    CONNECTIONS_CHANGED = "network.connections_changed"
    FS_CHANGED = "fs.changed"                        # paths=list[str] (folders whose contents changed)
    INDEX_UPDATED = "search.index_updated"           # root=str (a search index was rebuilt / patched)
    CLIPBOARD_CHANGED = "clipboard.changed"
    JOBS_CHANGED = "jobs.changed"                    # job=Job
    STATUS_MESSAGE = "status.message"                # text=str, level=str
    DEVICES_CHANGED = "devices.changed"              # old=list[Device], new=list[Device] (drives/mounts/cloud)
    DRIVE_USAGE_CHANGED = "drives.usage_changed"     # path=str (services/drive_usage.py measured a drive)
    HISTORY_CHANGED = "history.changed"              # the History tab's list changed (core/file_history.py)


class EventBus:
    """Synchronous publish/subscribe."""

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)

    def subscribe(self, topic: str, callback: Callable) -> Callable[[], None]:
        """Register ``callback(**data)`` for ``topic``.

        Returns a function that removes the subscription again, which widgets
        call when they are destroyed (so dead widgets never get events).
        """
        self._subscribers[topic].append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers[topic].remove(callback)
            except ValueError:
                pass
        return unsubscribe

    def publish(self, topic: str, **data) -> None:
        """Call every subscriber. One failing subscriber never stops the rest."""
        for callback in list(self._subscribers.get(topic, ())):
            try:
                callback(**data)
            except Exception:  # noqa: BLE001 - we really do want to keep going
                log.exception("Event handler for %r failed", topic)
