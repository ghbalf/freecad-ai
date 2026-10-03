"""Tests for MainThreadToolExecutor."""
import threading
import time
from unittest.mock import MagicMock

import pytest

from freecad_ai.tools.executor_utils import MainThreadToolExecutor
from freecad_ai.tools.registry import ToolDefinition, ToolRegistry, ToolResult


class TestMainThreadToolExecutor:
    def test_init(self):
        executor = MainThreadToolExecutor()
        assert executor._registry is None

    def test_set_registry(self):
        executor = MainThreadToolExecutor()
        mock_registry = MagicMock()
        executor.set_registry(mock_registry)
        assert executor._registry is mock_registry

    def test_do_execute_success(self):
        executor = MainThreadToolExecutor()
        mock_registry = MagicMock()
        expected = ToolResult(success=True, output="ok")
        mock_registry.execute.return_value = expected
        executor.set_registry(mock_registry)

        holder = {"result": None}
        executor._do_execute_sync("test_tool", {"arg": "val"}, holder)
        assert holder["result"] is expected
        mock_registry.execute.assert_called_once_with("test_tool", {"arg": "val"})

    def test_do_execute_exception_returns_error_result(self):
        executor = MainThreadToolExecutor()
        mock_registry = MagicMock()
        mock_registry.execute.side_effect = RuntimeError("FreeCAD crashed")
        executor.set_registry(mock_registry)

        holder = {"result": None}
        executor._do_execute_sync("bad_tool", {}, holder)
        assert holder["result"].success is False
        assert "FreeCAD crashed" in holder["result"].error

    def test_execute_direct_when_no_qt(self):
        """Without Qt dispatch, execute() runs directly on calling thread."""
        executor = MainThreadToolExecutor()
        mock_registry = MagicMock()
        expected = ToolResult(success=True, output="ok")
        mock_registry.execute.return_value = expected
        executor.set_registry(mock_registry)

        result = executor.execute("tool", {"x": 1})
        assert result is expected


# ── main_thread=False tools (#114) ─────────────────────────

@pytest.fixture(scope="module")
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _call_from_worker(qapp, fn):
    """Run fn on a plain thread while this (GUI) thread spins the event loop."""
    box = {}
    worker = threading.Thread(target=lambda: box.update(value=fn()))
    worker.start()
    deadline = time.monotonic() + 5
    while worker.is_alive() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    worker.join(1)
    assert not worker.is_alive(), "worker never finished"
    return box["value"], worker.ident


def _thread_recording_registry():
    seen = {}
    reg = ToolRegistry()
    for name, main in (("gui_tool", True), ("worker_tool", False)):
        reg.register(ToolDefinition(
            name=name, description=name, parameters=[], main_thread=main,
            handler=lambda _n=name: (seen.__setitem__(_n, threading.get_ident())
                                     or ToolResult(success=True, output=_n))))
    return reg, seen


def test_main_thread_defaults_to_true():
    tool = ToolDefinition(name="t", description="", parameters=[],
                          handler=lambda: None)
    assert tool.main_thread is True


def test_runs_on_caller_only_for_main_thread_false():
    executor = MainThreadToolExecutor()
    reg, _ = _thread_recording_registry()
    executor.set_registry(reg)
    assert executor._runs_on_caller("worker_tool") is True
    assert executor._runs_on_caller("gui_tool") is False
    assert executor._runs_on_caller("unknown") is False


def test_runs_on_caller_is_false_for_mock_registries():
    executor = MainThreadToolExecutor()
    executor.set_registry(MagicMock())
    assert executor._runs_on_caller("anything") is False


class TestQtDispatch:
    def test_ordinary_tool_runs_on_the_gui_thread(self, qapp):
        from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
        reg, seen = _thread_recording_registry()
        executor = QtMainThreadToolExecutor()
        executor.set_registry(reg)
        result, _ = _call_from_worker(qapp, lambda: executor.execute("gui_tool", {}))
        assert result.success and seen["gui_tool"] == threading.get_ident()

    def test_main_thread_false_tool_runs_on_the_calling_thread(self, qapp):
        from freecad_ai.tools.executor_utils import QtMainThreadToolExecutor
        reg, seen = _thread_recording_registry()
        executor = QtMainThreadToolExecutor()
        executor.set_registry(reg)
        result, worker_ident = _call_from_worker(
            qapp, lambda: executor.execute("worker_tool", {}))
        assert result.success and seen["worker_tool"] == worker_ident
