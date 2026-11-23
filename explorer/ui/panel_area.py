"""
PanelArea - holds one FilePanel, or two side by side (F3 "commander" split).

Pressing F3:
  * not split -> a right panel appears and the CURRENT tab moves into it
    (if it was the left side's only tab, the left side gets a fresh tab in
    the same folder, so both sides show something useful);
  * split -> the right side's tabs move back into the left panel.

The active panel is whichever one contains the keyboard focus. In split mode
it is marked with an accent line.
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter, QVBoxLayout, QWidget

from ..core.events import Topics
from .panel import FilePanel


class PanelArea(QWidget):
    def __init__(self, ctx, parent=None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(1)        # a 1px line; Qt still gives it a wider grab area
        layout.addWidget(self.splitter)
        self.panels: List[FilePanel] = []
        self.active_panel: Optional[FilePanel] = None
        self._add_panel()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

    @property
    def is_split(self) -> bool:
        return len(self.panels) > 1

    def _add_panel(self) -> FilePanel:
        panel = FilePanel(self.ctx, self)
        self.splitter.addWidget(panel)
        self.panels.append(panel)
        if self.active_panel is None:
            self.active_panel = panel
        return panel

    def other_panel(self, panel: Optional[FilePanel] = None) -> Optional[FilePanel]:
        panel = panel or self.active_panel
        return next((p for p in self.panels if p is not panel), None)

    def _on_focus_changed(self, old, new) -> None:
        widget = new
        while widget is not None:
            if isinstance(widget, FilePanel) and widget in self.panels:
                self.set_active(widget)
                return
            widget = widget.parentWidget()

    def set_active(self, panel: FilePanel, force_event: bool = False) -> None:
        changed = panel is not self.active_panel
        self.active_panel = panel
        for p in self.panels:
            p.set_highlight(self.is_split and p is panel)
        if (changed or force_event) and panel.active_tab is not None:
            tab = panel.active_tab
            self.ctx.bus.publish(Topics.ACTIVE_LOCATION_CHANGED, path=tab.path, tab=tab)
            self.ctx.bus.publish(Topics.SELECTION_CHANGED, tab=tab)

    # -- split ---------------------------------------------------------------
    def toggle_split(self) -> None:
        if self.is_split:
            self.unsplit()
        else:
            self.split()

    def split(self) -> None:
        if self.is_split:
            return
        left = self.panels[0]
        right = self._add_panel()
        tab = left.active_tab
        if tab is not None:
            if len(left.tabs) == 1:
                left.open_tab(state={"path": tab.path}, activate=False)
            left.move_tab_to_panel(tab, right)
        else:
            right.open_tab(None)
        self.splitter.setSizes([1, 1])
        right.set_active_panel(force_event=True)
        if right.active_tab:
            right.active_tab.focus_view()

    def unsplit(self) -> None:
        if not self.is_split:
            return
        left, right = self.panels[0], self.panels[1]
        was_active = self.active_panel is right
        active_tab = right.active_tab
        for tab in right.tabs:
            right.take_tab(tab)
            left.add_existing(tab, activate=False)
        if was_active and active_tab is not None:
            left.activate(left.tab_widget.indexOf(active_tab))
        self.remove_panel(right)

    def remove_panel(self, panel: FilePanel) -> None:
        if panel not in self.panels or len(self.panels) == 1:
            return
        for tab in panel.tabs:
            panel.take_tab(tab)
            tab.shutdown()
            tab.deleteLater()
        self.panels.remove(panel)
        panel.hide()
        panel.setParent(None)
        panel.deleteLater()
        self.active_panel = None
        self.set_active(self.panels[0], force_event=True)

    # -- session ---------------------------------------------------------------
    def get_state(self) -> dict:
        return {
            "split": self.is_split,
            "active_panel": self.panels.index(self.active_panel) if self.active_panel in self.panels else 0,
            "split_sizes": self.splitter.sizes(),
            "panels": [p.get_state() for p in self.panels],
        }

    def restore(self, state: Optional[dict]) -> None:
        panels_state = (state or {}).get("panels") or [{}]
        self.panels[0].restore(panels_state[0])
        if (state or {}).get("split") and len(panels_state) > 1:
            right = self._add_panel()
            right.restore(panels_state[1])
            sizes = state.get("split_sizes") or [1, 1]
            self.splitter.setSizes([int(s) for s in sizes])
        idx = int((state or {}).get("active_panel", 0) or 0)
        target = self.panels[min(idx, len(self.panels) - 1)]
        self.active_panel = None
        target.set_active_panel(force_event=True)
        if target.active_tab:
            target.active_tab.focus_view()
