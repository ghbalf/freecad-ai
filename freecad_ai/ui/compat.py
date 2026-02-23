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
