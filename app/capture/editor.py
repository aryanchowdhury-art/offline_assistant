import ctypes
import os
from dataclasses import dataclass
from pathlib import Path


_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".pytest_cache",
}

_TITLE_PARSERS = [
    ("Visual Studio Code", " - "),
    ("Notepad", " - "),
]

_SELF_TITLE_MARKERS = (
    "Offline Dual-Memory Assistant",
    "OfflineAssistant",
)


@dataclass
class EditorContext:
    window_title: str
    file_path: Path | None
    file_content: str | None
    error: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.file_path is not None and self.file_content is not None


def _get_foreground_window_title() -> str:
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _parse_title(title: str) -> str | None:
    for marker, sep in _TITLE_PARSERS:
        if marker in title:
            head = title.split(sep, 1)[0]
            name = head.lstrip("●*").strip()
            return name or None
    return None


def _walk_depth(root: Path, max_depth: int):
    root_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        current_depth = len(Path(dirpath).parts) - root_depth
        if current_depth >= max_depth:
            dirnames.clear()
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        yield Path(dirpath), filenames


def _find_file_in_roots(filename: str, roots: list[Path]) -> Path | None:
    for root in roots:
        if not root.exists():
            continue
        direct = root / filename
        if direct.is_file():
            return direct
        for dirpath, filenames in _walk_depth(root, max_depth=6):
            if filename in filenames:
                return dirpath / filename
    return None


def capture_editor_context(workspace_roots: list[Path]) -> EditorContext:
    title = _get_foreground_window_title()

    if any(marker in title for marker in _SELF_TITLE_MARKERS):
        return EditorContext(
            window_title=title,
            file_path=None,
            file_content=None,
            error=(
                "The assistant window is currently focused.\n\n"
                "Click on VS Code (or Notepad) first so it becomes the active "
                "window, then press Ctrl+Shift+Space again.\n\n"
                "Alternatively, use the 'Help Me' button — it will ask you "
                "which file to read."
            ),
        )

    filename = _parse_title(title)

    if not filename:
        return EditorContext(
            window_title=title,
            file_path=None,
            file_content=None,
            error=(
                "Active window is not a recognized editor "
                "(VS Code or Notepad).\n\n"
                f"Detected title: {title or '(empty)'}\n\n"
                "Click your editor window first, then try again."
            ),
        )

    if filename.lower().startswith("untitled"):
        return EditorContext(
            window_title=title,
            file_path=None,
            file_content=None,
            error=(
                "The editor has an unsaved buffer ('Untitled').\n"
                "Save the file first (Ctrl+S) with a name inside your "
                "workspace root, then try again."
            ),
        )

    file_path = _find_file_in_roots(filename, workspace_roots)
    if not file_path:
        return EditorContext(
            window_title=title,
            file_path=None,
            file_content=None,
            error=(
                f"Could not locate '{filename}' under configured workspace roots.\n\n"
                "Searched in:\n"
                + "\n".join(f"  - {r}" for r in workspace_roots)
                + "\n\nIf the file lives elsewhere, add its folder to "
                "vscode_workspace_roots in config.py."
            ),
        )

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return EditorContext(
            window_title=title,
            file_path=file_path,
            file_content=None,
            error=f"Failed to read file: {exc}",
        )

    return EditorContext(
        window_title=title,
        file_path=file_path,
        file_content=content,
    )