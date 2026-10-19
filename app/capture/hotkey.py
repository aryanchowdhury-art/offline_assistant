from PyQt6.QtCore import QObject, pyqtSignal


class _HotkeyEmitter(QObject):
    triggered = pyqtSignal()


_emitter: _HotkeyEmitter | None = None
_listener = None


def register_global_hotkey(combo: str = "<ctrl>+<shift>+<space>") -> bool:
    global _emitter, _listener

    try:
        from pynput import keyboard  # type: ignore
    except ImportError:
        return False

    _emitter = _HotkeyEmitter()

    def _fire():
        if _emitter is not None:
            _emitter.triggered.emit()

    _listener = keyboard.GlobalHotKeys({combo: _fire})
    _listener.daemon = True
    _listener.start()
    return True


def on_global_hotkey(callback) -> None:
    if _emitter is not None:
        _emitter.triggered.connect(callback)


def stop_global_hotkey() -> None:
    global _listener
    if _listener is not None:
        try:
            _listener.stop()
        except Exception:
            pass
        _listener = None