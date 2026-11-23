"""
font_picker - a drop-down of the fonts installed on this computer.

    combo = FontCombo(current="Ubuntu")                  # any font
    combo = FontCombo(current="", monospace=True)        # only fixed-width fonts (terminal)
    combo.family()   -> "Ubuntu", or "" for "System default"

Each name is shown in its own typeface, so you can see what you pick. Fonts
that can't show Latin letters (symbol, emoji and many script-specific fonts)
are listed in the normal font instead - otherwise their names would be
unreadable squiggles. Type the first letters of a name to jump to it.
"""
from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QComboBox

DB = QFontDatabase


def installed_families(monospace: bool = False) -> List[str]:
    """Sorted font names, without the system's hidden/private ones."""
    out = []
    for family in DB.families():
        if family.startswith(".") or DB.isPrivateFamily(family):
            continue
        if monospace and not (DB.isFixedPitch(family) and DB.WritingSystem.Latin in DB.writingSystems(family)):
            continue                            # (emoji fonts claim to be fixed-width too)
        out.append(family)
    return sorted(set(out), key=str.lower)


def system_default(monospace: bool = False) -> str:
    kind = DB.SystemFont.FixedFont if monospace else DB.SystemFont.GeneralFont
    return DB.systemFont(kind).family()


class FontCombo(QComboBox):
    def __init__(self, current: str = "", monospace: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setMaxVisibleItems(20)
        self.addItem(f"System default ({system_default(monospace)})", "")
        for family in installed_families(monospace):
            self.addItem(family, family)
            if DB.WritingSystem.Latin in DB.writingSystems(family):
                self.setItemData(self.count() - 1, QFont(family), Qt.ItemDataRole.FontRole)
        index = self.findData(current or "")
        if index < 0 and current:               # saved font no longer installed: say so, keep it
            self.addItem(f"{current} (not installed)", current)
            index = self.count() - 1
        self.setCurrentIndex(max(0, index))

    def family(self) -> str:
        return self.currentData() or ""
