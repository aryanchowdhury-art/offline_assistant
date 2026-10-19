from PyQt6.QtWidgets import QApplication


def get_clipboard_text() -> str:
    cb = QApplication.clipboard()
    return cb.text() if cb else ""


def set_clipboard_text(text: str) -> None:
    cb = QApplication.clipboard()
    if cb:
        cb.setText(text)