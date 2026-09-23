"""A failing chat turn must leave a traceback behind (follow-up to #89).

``_LLMWorker.run`` catches everything and emits ``str(e)``. The chat then
shows one line with no file, no line number and no stack -- which is how
#89 arrived: ``Error: 'NoneType' object is not iterable``, from a codebase
with three places that could have produced it.

The short message stays; it is the right thing to show in a chat bubble.
What is added is a traceback on the log channel, which reaches FreeCAD's
Report view, so the next report of this shape names its own line.
"""

import logging
from unittest.mock import patch

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.ui import chat_widget as cw  # noqa: E402


class _Signal:
    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class _Worker:
    """The attributes ``run`` touches before it reaches its handler."""

    def __init__(self):
        self.conversation = None
        self.error_occurred = _Signal()


def test_run_logs_the_traceback_and_emits_the_short_message(caplog):
    worker = _Worker()
    boom = TypeError("'NoneType' object is not iterable")

    with patch("freecad_ai.llm.client.create_client_from_config", side_effect=boom):
        with caplog.at_level(logging.ERROR, logger="freecad_ai.ui.chat_widget"):
            cw._LLMWorker.run(worker)

    # The chat bubble keeps the short form.
    assert worker.error_occurred.emitted == [("'NoneType' object is not iterable",)]

    # The Report view gets what a bug report actually needs.
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.exc_info is not None
    assert record.exc_info[1] is boom
    text = caplog.text
    assert "Traceback" in text
    assert "TypeError" in text
