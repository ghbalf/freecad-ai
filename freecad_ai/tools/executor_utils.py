"""Main-thread tool execution dispatcher.

FreeCAD's C++ layer is not thread-safe -- tool calls that use App.ActiveDocument
or FreeCADGui must run on the main (GUI) thread. This module provides a shared
utility for dispatching tool calls from worker threads to the main thread.

Used by both _LLMWorker (chat agentic loop) and SkillEvaluator (headless
evaluation runs).
"""
import logging

logger = logging.getLogger(__name__)

try:
    from ..ui.compat import QtCore, Signal
    QObject = QtCore.QObject
    QMutex = QtCore.QMutex
    QWaitCondition = QtCore.QWaitCondition
    Qt = QtCore.Qt
    _HAS_QT = True
except ImportError:
    _HAS_QT = False

from .registry import ToolResult


class MainThreadToolExecutor:
    """Dispatches tool calls to the main thread and waits for results.

    Base class that executes directly on the calling thread.
    Use QtMainThreadToolExecutor for cross-thread dispatch.
    """

    def __init__(self):
        self._registry = None

    def set_registry(self, registry):
        self._registry = registry

    def execute(self, tool_name: str, args: dict) -> ToolResult:
        """Execute a tool. In base class, runs directly."""
        holder = {"result": None}
        self._do_execute_sync(tool_name, args, holder)
        return holder["result"]

    def _do_execute_sync(self, tool_name, args, holder):
        """Execute tool and store result. Never leaks exceptions."""
        try:
            holder["result"] = self._registry.execute(tool_name, args)
        except Exception as e:
            logger.error("Tool execution failed: %s -- %s", tool_name, e)
            holder["result"] = ToolResult(success=False, output="", error=str(e))


if _HAS_QT:
    class QtMainThreadToolExecutor(MainThreadToolExecutor, QObject):
        """Qt-aware version that dispatches tool calls to the main thread.

        Call execute() from any thread -- it blocks until the main thread
        completes execution and returns the result.

        If already on the main thread (e.g., inside optimize_iteration handler),
        executes directly to avoid deadlock.
        """
        _execute_signal = Signal(str, str, object)  # tool_name, args_json, holder

        def __init__(self):
            QObject.__init__(self)
            MainThreadToolExecutor.__init__(self)
            self._execute_signal.connect(self._on_execute, Qt.QueuedConnection)
            self._mutex = QMutex()
            self._condition = QWaitCondition()

        def execute(self, tool_name: str, args: dict) -> ToolResult:
            """Call from any thread. Blocks until main thread completes."""
            import json
            app = QtCore.QCoreApplication.instance()
            if app and QtCore.QThread.currentThread() == app.thread():
                # Already on main thread -- execute directly (avoids deadlock)
                holder = {"result": None}
                self._do_execute_sync(tool_name, args, holder)
                return holder["result"]
            # Cross-thread dispatch via signal
            holder = {"result": None}
            args_json = json.dumps(args)
            self._mutex.lock()
            self._execute_signal.emit(tool_name, args_json, holder)
            self._condition.wait(self._mutex)
            self._mutex.unlock()
            return holder["result"]

        def _on_execute(self, tool_name, args_json, holder):
            """Runs on main thread via queued signal connection."""
            import json
            args = json.loads(args_json)
            try:
                self._do_execute_sync(tool_name, args, holder)
            finally:
                self._mutex.lock()
                self._condition.wakeAll()
                self._mutex.unlock()
