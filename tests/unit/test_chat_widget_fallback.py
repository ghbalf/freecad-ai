"""Widget side of #104: one start path, Stop that frees the input."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

try:
    import PySide6  # noqa: F401
except ImportError:
    try:
        import PySide2  # noqa: F401
    except ImportError:
        pytest.skip("PySide6/PySide2 not available", allow_module_level=True)

from freecad_ai.core.conversation import Conversation  # noqa: E402
from freecad_ai.ui import chat_widget as cw  # noqa: E402

W = cw.ChatDockWidget


def _running_worker():
    worker = MagicMock()
    worker.isRunning.return_value = True
    worker._full_response = "partial"
    return worker


def _as_slot_host(fake, sender=None):
    """Give a fake the dock's current-worker guard, as a real slot sees it:
    ``sender`` is the emitting worker, None for a direct call."""
    fake.sender = lambda: sender
    fake._from_current_worker = lambda: W._from_current_worker(fake)
    return fake


def _detach_fake(worker):
    fake = SimpleNamespace(
        _worker=worker, _detached_workers=[],
        _store_tool_results=MagicMock(), conversation=MagicMock(),
        chat_display=MagicMock(), _set_loading=MagicMock(),
        _append_html=MagicMock(), _WORKER_SLOTS=W._WORKER_SLOTS,
        _turn_open=True)
    fake._release_detached = lambda w: W._release_detached(fake, w)
    fake._on_detached_finished = lambda: W._on_detached_finished(fake)
    fake.sender = lambda: worker
    return fake


def test_stop_interrupts_and_schedules_a_detach():
    worker = _running_worker()
    fake = SimpleNamespace(_worker=worker, _schedule_detach=MagicMock())
    W._send_message(fake)
    worker.requestInterruption.assert_called_once()
    fake._schedule_detach.assert_called_once_with(worker)


def test_a_stuck_worker_is_detached():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_called_once()
    worker.token_received.disconnect.assert_called()
    worker.fallback_note.disconnect.assert_called()
    fake._store_tool_results.assert_called_once_with("partial")
    fake.conversation.save.assert_called_once()
    assert fake._worker is None
    fake._set_loading.assert_called_once_with(False)
    assert fake._detached_workers == [worker]
    assert "⏹ Stopped" in fake._append_html.call_args[0][0]


def test_a_worker_that_finished_is_left_alone():
    worker = _running_worker()
    worker.isRunning.return_value = False
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_not_called()
    assert fake._worker is worker


def test_a_newer_worker_is_left_alone():
    old, new = _running_worker(), _running_worker()
    fake = _detach_fake(new)
    W._detach_if_stuck(fake, old)
    old.detach.assert_not_called()
    assert fake._worker is new


def test_a_detached_worker_is_released_when_it_ends():
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    finished_slot = worker.finished.connect.call_args[0][0]
    finished_slot()
    assert fake._detached_workers == []
    worker.deleteLater.assert_called_once()


def test_a_detached_worker_leaves_the_docks_ownership():
    """Qt deletes a dock's children at quit, and destroying a running
    QThread is qFatal. A detached worker can block for minutes, so the
    dock must not own it (live probe: exit 134 before this)."""
    worker = _running_worker()
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    worker.setParent.assert_called_once_with(None)


def _shutdown_fake(**workers):
    return SimpleNamespace(_shutting_down=False, _dock_poll_timer=MagicMock(),
                           _detached_workers=[], **workers)


def test_shutdown_keeps_every_running_worker_alive_and_unowned(monkeypatch):
    kept = []
    monkeypatch.setattr(cw, "_KEEP_UNTIL_EXIT", kept)
    current, compaction, detached = (_running_worker(), _running_worker(),
                                     _running_worker())
    fake = _shutdown_fake(_worker=current, _compaction_worker=compaction)
    fake._detached_workers.append(detached)
    W._mark_shutdown(fake)
    W._mark_shutdown(fake)          # main-window Close, then aboutToQuit
    for w in (current, compaction, detached):
        w.setParent.assert_called_with(None)
    assert kept == [current, compaction, detached]
    assert fake._shutting_down


def test_shutdown_leaves_finished_workers_alone(monkeypatch):
    kept = []
    monkeypatch.setattr(cw, "_KEEP_UNTIL_EXIT", kept)
    done = _running_worker()
    done.isRunning.return_value = False
    fake = _shutdown_fake(_worker=done)      # no compaction worker ever ran
    W._mark_shutdown(fake)
    done.setParent.assert_not_called()
    assert kept == []


def test_keep_until_exit_unparents_a_real_thread(monkeypatch):
    from freecad_ai.ui.compat import QtCore, QtWidgets
    _app = (QtWidgets.QApplication.instance()  # noqa: F841 -- keep alive
            or QtWidgets.QApplication([]))
    kept = []
    monkeypatch.setattr(cw, "_KEEP_UNTIL_EXIT", kept)
    owner = QtCore.QObject()
    thread = QtCore.QThread(owner)
    cw._keep_until_exit(thread)
    assert thread.parent() is None
    assert kept == [thread]
    assert owner.children() == []


def test_the_fallback_note_is_escaped():
    fake = _as_slot_host(SimpleNamespace(_append_html=MagicMock(),
                                         _turn_notes=[]))
    W._on_fallback_note(fake, "⚠ a<b couldn't be reached — answered by c")
    html = fake._append_html.call_args[0][0]
    assert "a&lt;b" in html
    assert "color: #888" in html


def test_start_worker_connects_every_slot(monkeypatch):
    made = {}

    class _Fake:
        def __init__(self, conversation, system_prompt, **kw):
            made.update(kw, conversation=conversation, system=system_prompt)
            self.connected = []
            for signal, _ in W._WORKER_SLOTS:
                setattr(self, signal, SimpleNamespace(
                    connect=lambda slot, s=signal: self.connected.append(s)))

        def start(self):
            made["started"] = True

    monkeypatch.setattr(cw, "_LLMWorker", _Fake)
    fake = SimpleNamespace(_set_loading=MagicMock(), _append_html=MagicMock(),
                           _WORKER_SLOTS=W._WORKER_SLOTS)
    for _, slot in W._WORKER_SLOTS:
        setattr(fake, slot, MagicMock())
    conv = Conversation()
    W._start_worker(fake, conv, "S", start_index=4)
    assert made["conversation"] is conv
    assert made["start_index"] == 4
    assert made["parent"] is fake
    assert made["started"] is True
    assert fake._worker.connected == [s for s, _ in W._WORKER_SLOTS]
    assert (fake._in_thinking, fake._tool_results_stored,
            fake._summary_rendered) == (False, False, False)


def test_the_retry_path_hands_over_the_conversation(monkeypatch, tmp_config_dir):
    """Bug 2: the retry rendered OpenAI style from cfg.provider.model."""
    monkeypatch.setattr("freecad_ai.core.system_prompt.build_system_prompt",
                        lambda **k: "SYS")
    from freecad_ai.config import get_config
    get_config().optimize_prompt_caching = False
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    started = {}
    fake = SimpleNamespace(
        _retry_count=0, conversation=conv, _capture_mode_override="off",
        _append_html=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: 1),
        _needs_vision=W._needs_vision,
        _start_worker=lambda c, s, **kw: started.update(kw, conv=c, system=s))
    W._handle_execution_error(fake, SimpleNamespace(stderr="boom"))
    assert started["conv"] is conv
    assert started["system"] == "SYS"
    assert started["start_index"] == conv.window_start()
    assert "api_style" not in started


@pytest.mark.parametrize("index, mode", [(0, "plan"), (1, "act")])
def test_the_retry_path_keeps_the_custom_prompt(monkeypatch, tmp_config_dir,
                                                index, mode):
    """The retry sends no tools, so it gets what a send without tools gets."""
    from freecad_ai.config import get_config
    from freecad_ai.core.system_prompt import build_system_prompt
    custom = "HOUSE RULE\n\n## Mode: Act (with Tools)\nCALL TOOLS\n"
    cfg = get_config()
    monkeypatch.setattr(cfg, "system_prompt_override", custom)
    monkeypatch.setattr(cfg, "optimize_prompt_caching", False)
    started = {}
    fake = SimpleNamespace(
        _retry_count=0,
        conversation=Conversation(messages=[{"role": "user", "content": "go"}]),
        _capture_mode_override="off", _append_html=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: index),
        _needs_vision=W._needs_vision,
        _start_worker=lambda c, s, **kw: started.update(kw, system=s))
    W._handle_execution_error(fake, SimpleNamespace(stderr="boom"))
    assert "HOUSE RULE" in started["system"]
    assert "CALL TOOLS" not in started["system"]
    assert started["system"] == build_system_prompt(mode=mode, override=custom)
    assert not {"tools", "registry"} & started.keys()


def test_needs_vision_comes_from_the_rendered_history():
    conv = Conversation()
    conv.add_user_message("look", images=[{"type": "image", "data": "AA",
                                           "mime_type": "image/png"}])
    conv.add_assistant_message("seen")
    conv.add_user_message("more")
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 0) is True
    assert W._needs_vision(SimpleNamespace(supports_vision=True), conv, 2) is False
    assert W._needs_vision(SimpleNamespace(supports_vision=False), conv, 0) is False


def test_the_session_log_carries_the_attempts(monkeypatch, tmp_config_dir):
    import json
    import os
    from freecad_ai import config as config_mod
    worker = SimpleNamespace(
        _tool_results=[{"assistant_text": "", "tool_calls": [], "results": []}],
        fallback_attempts=[{"round": 0, "profile": "a", "outcome": "failed",
                            "error": "HTTP 503"}])
    fake = SimpleNamespace(_worker=worker, conversation=Conversation(),
                           _append_html=MagicMock())
    # tmp_config_dir already points config_mod.LOGS_DIR at a temp dir;
    # chat_widget imported the real path by value, so follow it.
    monkeypatch.setattr(cw, "LOGS_DIR", config_mod.LOGS_DIR)
    W._save_session_log(fake)
    [name] = os.listdir(config_mod.LOGS_DIR)
    with open(os.path.join(config_mod.LOGS_DIR, name)) as f:
        assert json.load(f)["fallback_attempts"][0]["error"] == "HTTP 503"


# ── Fix round 1 ──────────────────────────────────────────────

NOTE = "⚠ a<b couldn't be reached — answered by c"


def _note_fake(conv):
    return _as_slot_host(SimpleNamespace(
        conversation=conv, _worker=SimpleNamespace(_tool_results=[]),
        _turn_notes=[], _turn_start=len(conv.messages),
        _append_html=MagicMock(), chat_display=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: 1)))


def _rendered(fake):
    W._rerender_chat(fake)
    return fake.chat_display.setHtml.call_args[0][0]


def test_a_fallback_note_survives_the_rerender():
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    fake = _note_fake(conv)
    W._on_fallback_note(fake, NOTE)
    W._store_tool_results(fake, "THE-ANSWER")
    html = _rendered(fake)
    assert "a&lt;b" in html
    assert html.index("a&lt;b") < html.index("THE-ANSWER")


def test_two_notes_in_one_turn_both_survive_in_order():
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    fake = _note_fake(conv)
    W._on_fallback_note(fake, "FIRST-NOTE")
    W._on_fallback_note(fake, "SECOND-NOTE")
    W._store_tool_results(fake, "THE-ANSWER")
    html = _rendered(fake)
    assert (html.index("FIRST-NOTE") < html.index("SECOND-NOTE")
            < html.index("THE-ANSWER"))
    assert html.count("FIRST-NOTE") == 1


def test_the_notes_move_to_the_error_summary_and_show_once(monkeypatch):
    conv = Conversation(messages=[{"role": "user", "content": "go"}])
    fake = _note_fake(conv)
    fake._worker = SimpleNamespace(_tool_results=[{
        "assistant_text": "", "results": [{"tool_call_id": "c1",
                                           "content": "ok"}],
        "tool_calls": [{"id": "c1", "name": "t", "arguments": {}}]}])
    fake._set_loading = MagicMock()
    fake._auto_save_log = MagicMock()
    fake._store_tool_results = lambda r="": W._store_tool_results(fake, r)
    monkeypatch.setattr(Conversation, "save", lambda self: None)
    W._on_fallback_note(fake, "ONLY-NOTE")
    W._on_error(fake, "boom")
    carrying = [m for m in conv.messages if m.get("notes")]
    assert carrying == [conv.messages[-1]]
    assert _rendered(fake).count("ONLY-NOTE") == 1


def test_notes_never_reach_a_request_body():
    import json
    conv = Conversation(messages=[
        {"role": "user", "content": "go", "doc_context": "DOC"},
        {"role": "assistant", "content": "", "notes": ["N-TOOLS"],
         "tool_calls": [{"id": "c1", "name": "t", "arguments": {}}]},
        {"role": "tool_result", "tool_call_id": "c1", "content": "ok"},
        {"role": "assistant", "content": "plain", "notes": ["N-PLAIN"],
         "reasoning_content": "R"},
        {"role": "user", "content": [
            {"type": "text", "text": "look"},
            {"type": "image", "media_type": "image/png", "data": "AA"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "blocks"}],
         "notes": ["N-BLOCKS"]},
        {"role": "user", "content": "more"},
    ])
    for style in ("openai", "anthropic"):
        for kw in ({}, {"strip_images": True},
                   {"describe_fn": lambda b: "described"}):
            body = json.dumps(conv.get_messages_for_api(api_style=style, **kw))
            assert "N-" not in body and "notes" not in body, (style, kw)


def test_notes_survive_a_session_save_and_load(tmp_config_dir):
    conv = Conversation(messages=[
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "a", "notes": ["KEPT"]}])
    conv.save()
    loaded = Conversation.load(conv.conversation_id)
    assert loaded.messages[-1]["notes"] == ["KEPT"]


class _SignalWorker:
    """Records the callable each signal was connected to."""

    def __init__(self, conversation, system_prompt, **kw):
        self.slots = {}
        for signal, _ in W._WORKER_SLOTS:
            setattr(self, signal, SimpleNamespace(
                connect=lambda fn, s=signal: self.slots.__setitem__(s, fn)))

    def start(self):
        pass


def _started(monkeypatch):
    monkeypatch.setattr(cw, "_LLMWorker", _SignalWorker)
    fake = SimpleNamespace(_set_loading=MagicMock(), _append_html=MagicMock(),
                           _WORKER_SLOTS=W._WORKER_SLOTS)
    for _, slot in W._WORKER_SLOTS:
        setattr(fake, slot, MagicMock())
    W._start_worker(fake, Conversation(), "S")
    return fake, fake._worker


def test_start_worker_connects_the_dock_slots_themselves(monkeypatch):
    """Bound dock methods, as before #104: a QObject receiver gets its
    slots queued onto the GUI thread under PySide2 and PySide6 alike."""
    fake, worker = _started(monkeypatch)
    for signal, slot in W._WORKER_SLOTS:
        assert worker.slots[signal] is getattr(fake, slot)


def test_a_signal_from_the_current_worker_is_handled():
    worker = object()
    fake = _as_slot_host(SimpleNamespace(_worker=worker,
                                         _append_html=MagicMock()), worker)
    W._on_vision_note(fake, "seen")
    fake._append_html.assert_called_once()


@pytest.mark.parametrize("current", [None, "newer"])
@pytest.mark.parametrize("signal,slot", W._WORKER_SLOTS)
def test_a_signal_from_an_old_worker_changes_nothing(signal, slot, current):
    """The fake holds only the guard's inputs: a slot that went past the
    guard would hit an AttributeError."""
    old = object()
    fake = _as_slot_host(SimpleNamespace(
        _worker=None if current is None else object()), old)
    args = {"tool_call_finished": ("t", "c", True, "o"),
            "tool_call_started": ("t", "c"),
            "context_note": ("k", "t"),
            "tool_exec_requested": ("t", "{}")}.get(signal, ("x",))
    assert getattr(W, slot)(fake, *args) is None


def test_a_real_queued_signal_from_a_stale_thread_is_dropped():
    """Real QThread emitter, real QObject receiver, real queued delivery:
    sender() names the thread, and the guard drops it once replaced."""
    import threading
    from freecad_ai.ui.compat import QtCore, QtWidgets
    _app = (QtWidgets.QApplication.instance()  # noqa: F841 -- keep alive
            or QtWidgets.QApplication([]))

    class _Thread(QtCore.QThread):
        vision_note = QtCore.Signal(str)

        def run(self):
            self.vision_note.emit("hello")

    class _Dock(QtCore.QObject):
        _from_current_worker = W._from_current_worker

        def _on_vision_note(self, message):
            # Delegate: PySide skips a foreign function set as a class
            # attribute, so the slot must be defined here.
            self.delivered += 1
            return W._on_vision_note(self, message)

        def __init__(self):
            super().__init__()
            self.seen = []
            self.delivered = 0

        def _append_html(self, html):
            self.seen.append((html, threading.current_thread()
                              is threading.main_thread()))

    for replaced in (False, True):
        dock, thread = _Dock(), _Thread()
        dock._worker = None if replaced else thread
        thread.vision_note.connect(dock._on_vision_note)
        thread.start()
        thread.wait(5000)
        # Deliver the queued call now (processEvents alone may not, on a
        # real display platform).
        QtCore.QCoreApplication.sendPostedEvents()
        assert dock.delivered == 1, "the queued call must really arrive"
        if replaced:
            assert dock.seen == []
        else:
            [(html, on_main)] = dock.seen
            assert "hello" in html and on_main


def test_a_direct_call_is_handled():
    fake = _as_slot_host(SimpleNamespace(_worker=object(),
                                         _append_html=MagicMock()))
    W._on_vision_note(fake, "seen")
    fake._append_html.assert_called_once()


def test_starting_a_worker_opens_the_turn(monkeypatch):
    fake, _ = _started(monkeypatch)
    assert fake._turn_open is True
    assert fake._turn_notes == []


def test_an_error_closes_the_turn():
    fake = _as_slot_host(SimpleNamespace(
        _turn_open=True, _set_loading=MagicMock(), chat_display=MagicMock(),
        _store_tool_results=MagicMock(), conversation=Conversation(),
        _worker=SimpleNamespace(_tool_results=[]), _append_html=MagicMock()))
    W._on_error(fake, "boom")
    assert fake._turn_open is False


def test_a_finished_response_closes_the_turn(monkeypatch):
    monkeypatch.setattr("freecad_ai.hooks.fire_hook", lambda *a, **k: None)
    fake = _as_slot_host(SimpleNamespace(
        _turn_open=True, _set_loading=MagicMock(), chat_display=MagicMock(),
        _store_tool_results=MagicMock(), conversation=MagicMock(),
        _update_token_count=MagicMock(), _rerender_chat=MagicMock(),
        mode_combo=SimpleNamespace(currentIndex=lambda: 1),
        _capture_mode_override="off", _append_html=MagicMock(),
        _worker=SimpleNamespace(_tool_results=[], _response_truncated=False,
                                _tool_timeline=[])))
    W._on_response_finished(fake, "")
    assert fake._turn_open is False


def test_a_detach_after_the_turn_closed_does_nothing():
    worker = _running_worker()
    fake = _detach_fake(worker)
    fake._turn_open = False
    W._detach_if_stuck(fake, worker)
    worker.detach.assert_not_called()
    fake._store_tool_results.assert_not_called()
    fake._append_html.assert_not_called()
    assert fake._worker is worker


def test_save_log_after_a_detach_keeps_the_trace(monkeypatch, tmp_config_dir):
    import json
    import os
    from freecad_ai import config as config_mod
    worker = _running_worker()
    worker._tool_results = [{"assistant_text": "", "tool_calls": [],
                             "results": []}]
    worker.fallback_attempts = [{"round": 0, "profile": "a",
                                 "outcome": "failed", "error": "HTTP 503"}]
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    fake.conversation = Conversation()
    monkeypatch.setattr(cw, "LOGS_DIR", config_mod.LOGS_DIR)
    W._save_session_log(fake)
    [name] = os.listdir(config_mod.LOGS_DIR)
    with open(os.path.join(config_mod.LOGS_DIR, name)) as f:
        data = json.load(f)
    assert data["fallback_attempts"][0]["error"] == "HTTP 503"
    assert data["tool_trace"] == worker._tool_results


def test_a_thread_that_ended_during_the_detach_is_released():
    worker = _running_worker()
    worker.isRunning.side_effect = [True, False]
    fake = _detach_fake(worker)
    W._detach_if_stuck(fake, worker)
    assert fake._detached_workers == []
    worker.deleteLater.assert_called_once()


def test_the_detach_timer_is_a_child_of_the_dock(monkeypatch):
    """A child QTimer dies with the dock, under PySide2 and PySide6; the
    singleShot(ms, context, fn) overload is not in every binding."""
    timer_cls = MagicMock()
    monkeypatch.setattr(cw, "QtCore", SimpleNamespace(QTimer=timer_cls))
    fake = SimpleNamespace(_detach_pending={}, _on_detach_timeout=MagicMock())
    worker = object()
    W._schedule_detach(fake, worker)
    timer_cls.assert_called_once_with(fake)
    timer = timer_cls.return_value
    timer.setSingleShot.assert_called_once_with(True)
    timer.timeout.connect.assert_called_once_with(fake._on_detach_timeout)
    timer.start.assert_called_once_with(cw._DETACH_AFTER_MS)
    assert fake._detach_pending == {timer: worker}


def test_the_detach_timeout_detaches_its_own_worker():
    first, second = MagicMock(), MagicMock()
    w1, w2 = object(), object()
    fake = SimpleNamespace(_detach_pending={first: w1, second: w2},
                           _detach_if_stuck=MagicMock(),
                           sender=lambda: second)
    W._on_detach_timeout(fake)
    fake._detach_if_stuck.assert_called_once_with(w2)
    assert fake._detach_pending == {first: w1}
    second.deleteLater.assert_called_once()
