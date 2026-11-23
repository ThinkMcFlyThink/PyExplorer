"""
PyExplorer - a modular, cross-platform file explorer written in Python + Qt (PySide6).

Package layout (each layer only depends on the layers *above* it in this list):

    core/      Plain-Python building blocks. No Qt. Easy to unit-test.
               (event bus, settings, background tasks, data models, stores)
    vfs/       "Virtual file systems": local disk, Computer/drives, SFTP, FTP.
               Everything else talks to files ONLY through this interface,
               which is why network locations behave like local folders.
    services/  Work that runs off the UI thread: directory scanning, search,
               file operations, folder watching, terminal shells.
    ui/        Qt widgets. Each widget gets what it needs through an
               ``AppContext`` object instead of reaching into other widgets.
    commands.py  Every user-level operation (copy, rename, new tab...) in one
               place. Menus, shortcuts and context menus all call into it.
    actions.py   Maps action ids -> label, keyboard shortcut, command.
    app.py       The "composition root": creates everything and wires it up.
"""

APP_NAME = "PyExplorer"
APP_VERSION = "0.8.1"
