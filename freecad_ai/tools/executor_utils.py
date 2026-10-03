"""Main-thread tool execution dispatcher.

FreeCAD's C++ layer is not thread-safe -- tool calls that use App.ActiveDocument
or FreeCADGui must run on the main (GUI) thread. This module provides a shared
utility for dispatching tool calls from worker threads to the main thread.

Used by both _LLMWorker (chat agentic loop) and SkillEvaluator (headless
evaluation runs).
"""
import logging
import threading

logger = logging.getLogger(__name__)

try:
    from ..ui.compat import QtCore
    Signal = QtCore.Signal
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

    def _runs_on_caller(self, tool_name):
        """True for tools registered with main_thread=False."""
        tool = self._registry.get(tool_name) if self._registry is not None else None
        return getattr(tool, "main_thread", True) is False

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
            on_main = app and QtCore.QThread.currentThread() == app.thread()
            if on_main or self._runs_on_caller(tool_name):
                # On the main thread already (avoids deadlock), or a tool that
                # must not block it (main_thread=False)
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


_caller = None
_caller_lock = threading.Lock()

if _HAS_QT:
    class _MainThreadCaller(QObject):
        """Runs callables on the GUI thread for run_on_main."""
        _call = Signal(object)

        def __init__(self):
            super().__init__()
            self._call.connect(self._run, Qt.QueuedConnection)

        def _run(self, box):
            try:
                box["value"] = box["fn"]()
            except BaseException as e:  # handed to the waiting thread
                box["error"] = e
            finally:
                box["done"].set()

        def call(self, fn):
            box = {"fn": fn, "done": threading.Event()}
            self._call.emit(box)
            box["done"].wait()
            if "error" in box:
                raise box["error"]
            return box.get("value")


def run_on_main(fn):
    """Call ``fn()`` on the GUI thread from any thread and return its result.

    For main_thread=False tool handlers, which run on a worker thread but
    still need FreeCAD's document API for a moment. Calls ``fn`` directly
    without Qt, without an application object, or on the GUI thread itself.
    """
    global _caller
    if not _HAS_QT:
        return fn()
    app = QtCore.QCoreApplication.instance()
    if app is None or QtCore.QThread.currentThread() == app.thread():
        return fn()
    with _caller_lock:
        if _caller is None:
            _caller = _MainThreadCaller()
            # Created on whichever thread got here first; queued calls must
            # be delivered on the GUI thread.
            _caller.moveToThread(app.thread())
    return _caller.call(fn)


def current_thread_interrupted():
    """True once the chat's Stop button asked the calling QThread to stop."""
    if not _HAS_QT:
        return False
    try:
        return bool(QtCore.QThread.currentThread().isInterruptionRequested())
    except Exception:
        return False
