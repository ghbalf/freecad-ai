"""PySide2/PySide6 compatibility shim.

FreeCAD 1.0+ ships PySide6, but older builds and some AppImages
still use PySide2. This module re-exports Qt modules from whichever
is available.
"""

try:
    from PySide6 import QtWidgets, QtCore, QtGui  # noqa: F401
    PYSIDE_VERSION = 6
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui  # noqa: F401
    PYSIDE_VERSION = 2

# Marks a string literal for extraction by pylupdate without translating it
# where it is written — for labels declared in a module-level table and
# passed through translate() at the use site. pylupdate only ever sees
# literals, so translate(some_variable) extracts nothing at all.
# getattr, not an attribute access: both bindings return the text unchanged,
# so the fallback is an identity function rather than a degraded path.
QT_TRANSLATE_NOOP = getattr(
    QtCore, "QT_TRANSLATE_NOOP", lambda context, text: text)
