"""
The Settings tab (Ctrl+,): every setting, applied as you change it.

    core/settings_schema.py   WHAT is on each page (plain data)
    tab.py                    the tab: page list, search box, pages of rows
    editors.py                one small editor per kind of setting (+ the on/off Switch)
    keyboard.py               Keyboard page: every command's shortcut
    toolbar_page.py           Toolbar page: which buttons, in which order
    logins.py                 Network page: the logins PyExplorer remembers

The tab is registered as the "settings" utility in ui/utility/__init__.py.
"""
