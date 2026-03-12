"""Viewport capture and image utilities.

Provides helpers for capturing the FreeCAD 3D viewport and
converting/resizing images for use in the chat pipeline.
"""

import base64
import os
import tempfile

# Resolution presets: name -> (width, height)
RESOLUTION_PRESETS = {
    "low": (400, 300),
    "medium": (800, 600),
    "high": (1280, 960),
}


def capture_viewport_image(width: int = 800, height: int = 600,
                           background: str = "Current") -> bytes | None:
    """Capture the current 3D viewport as PNG bytes.

    Returns None if no active view is available.
    """
    try:
        import FreeCADGui as Gui
    except ImportError:
        return None

    if not Gui.ActiveDocument:
        return None

    view = Gui.ActiveDocument.ActiveView
    if view is None:
        return None

    # Save to a temp file, read bytes, clean up
    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        view.saveImage(tmp_path, width, height, background)
        with open(tmp_path, "rb") as f:
            return f.read()
    except Exception:
        return None
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def resize_image_bytes(data: bytes, max_width: int, max_height: int) -> bytes:
    """Resize image bytes (any format) to fit within max dimensions, preserving aspect ratio.

    Returns PNG bytes. If the image already fits, it is returned as PNG without upscaling.
    Uses QImage from Qt (available in FreeCAD environment).
    """
    from ..ui.compat import QtGui, QtCore

    img = QtGui.QImage()
    img.loadFromData(data)
    if img.isNull():
        return data  # Can't decode — return original

    w, h = img.width(), img.height()
    if w <= max_width and h <= max_height:
        # Already fits — just convert to PNG if not already
        pass
    else:
        img = img.scaled(max_width, max_height, QtCore.Qt.KeepAspectRatio,
                         QtCore.Qt.SmoothTransformation)

    # Convert QImage to PNG bytes
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def image_to_base64_png(image_bytes: bytes) -> str:
    """Encode raw image bytes as a base64 string."""
    return base64.b64encode(image_bytes).decode("ascii")


def make_image_content_block(image_bytes: bytes) -> dict:
    """Create an image content block dict from raw image bytes.

    Returns a dict suitable for inclusion in a message's content list:
    {"type": "image", "source": "base64", "media_type": "image/png", "data": "..."}
    """
    return {
        "type": "image",
        "source": "base64",
        "media_type": "image/png",
        "data": image_to_base64_png(image_bytes),
    }
