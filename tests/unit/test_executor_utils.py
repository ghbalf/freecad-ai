"""Tests for MainThreadToolExecutor."""
from unittest.mock import MagicMock
from freecad_ai.tools.executor_utils import MainThreadToolExecutor
from freecad_ai.tools.registry import ToolResult


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
