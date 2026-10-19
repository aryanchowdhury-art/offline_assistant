import os
import re
from pathlib import Path

from PyQt6.QtCore import QSettings, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.capture.clipboard import get_clipboard_text, set_clipboard_text
from app.capture.editor import EditorContext, capture_editor_context
from app.capture.hotkey import on_global_hotkey, register_global_hotkey
from app.capture.project import (
    build_outline,
    find_files_defining_symbol,
    find_related_files,
)
from app.core.assistant import OfflineAssistant, extract_code_blocks
from app.core.file_actions import apply_write, resolve_write_path
from app.core.types import AssistantMode
from app.ui.code_approval import CodeApprovalDialog, FileWriteApprovalDialog
from app.ui.onboarding import OnboardingDialog
from app.ui.worker import AssistantWorker


_SETTINGS_ORG = "OfflineAssistant"
_SETTINGS_APP = "OfflineAssistant"

_FILENAME_RE = re.compile(
    r"\b([A-Za-z_][\w./\\-]*\.(?:"
    r"py|pyw|js|ts|jsx|tsx|html|css|md|txt|json|toml|yaml|yml|"
    r"sh|bat|ps1|cfg|ini|c|h|cpp|hpp|cc|java|go|rs|rb|php"
    r"))\b",
    re.IGNORECASE,
)

_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages",
}


class StatusPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)

        self.label = QLabel("🟢 IDLE")
        self.label.setStyleSheet(
            "font-weight: bold; font-size: 13px; padding: 2px 6px;"
        )
        layout.addWidget(self.label)

    def set_status(self, status: str) -> None:
        self.label.setText(status)


class MainWindow(QMainWindow):
    send_requested = pyqtSignal(str, str)
    copilot_requested = pyqtSignal(str, str, str, str, list)

    def __init__(self, assistant: OfflineAssistant):
        super().__init__()
        self.assistant = assistant
        self.session_id = "default"
        self._current_attachment_path: str | None = None
        self._current_related: list[tuple[str, str]] = []

        self._settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self._extra_roots: list[Path] = self._load_extra_roots()
        self._current_root: Path = self._load_current_root()

        self._setup_ui()
        self._setup_thread()
        self._setup_hotkeys()
        self._refresh_project_menu()
        self._update_title()

    # ---------- settings ----------
    def _load_extra_roots(self) -> list[Path]:
        raw = self._settings.value("extra_roots", [])
        if isinstance(raw, str):
            raw = [raw]
        if not raw:
            return []
        out: list[Path] = []
        for s in raw:
            try:
                p = Path(str(s))
                if p.exists():
                    out.append(p)
            except Exception:
                continue
        return out

    def _save_extra_roots(self) -> None:
        self._settings.setValue("extra_roots", [str(p) for p in self._extra_roots])

    def _load_current_root(self) -> Path:
        saved = self._settings.value("current_root", "")
        if saved:
            try:
                p = Path(str(saved))
                if p.exists():
                    return p
            except Exception:
                pass
        return self._primary_default_root()

    def _save_current_root(self) -> None:
        self._settings.setValue("current_root", str(self._current_root))

    # ---------- roots ----------
    def _primary_default_root(self) -> Path:
        roots = self.assistant.config.vscode_workspace_roots
        return roots[0] if roots else self.assistant.config.base_dir

    def _all_roots(self) -> list[Path]:
        roots: list[Path] = []
        seen: set[str] = set()
        for p in list(self.assistant.config.vscode_workspace_roots) + list(self._extra_roots):
            try:
                key = str(p.resolve()).lower()
            except Exception:
                continue
            if key in seen:
                continue
            seen.add(key)
            roots.append(p)
        return roots

    def _primary_root(self) -> Path:
        return self._current_root

    def _root_for_file(self, path: Path) -> Path:
        try:
            target = path.resolve()
        except Exception:
            return self._primary_root()
        for root in self._all_roots():
            try:
                target.relative_to(root.resolve())
                return root
            except Exception:
                continue
        return self._primary_root()

    # ---------- UI ----------
    def _setup_ui(self) -> None:
        self.setWindowTitle("Offline Dual-Memory Assistant")
        self.resize(1050, 780)

        profile_menu = self.menuBar().addMenu("Profile")
        profile_menu.addAction("Edit my profile…", self._open_onboarding)
        profile_menu.addAction("Show my profile…", self._show_profile)

        copilot_menu = self.menuBar().addMenu("Copilot")
        copilot_menu.addAction(
            "Help with current file  (Ctrl+Shift+Space)", self._trigger_copilot
        )
        copilot_menu.addAction("Pick a file to analyze…", self._trigger_copilot_pick)
        copilot_menu.addAction("Snapshot project outline", self._snapshot_project)

        self._project_menu = copilot_menu.addMenu("Project")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.status_panel = StatusPanel()
        layout.addWidget(self.status_panel)

        self.project_label = QLabel("")
        self.project_label.setStyleSheet(
            "color: #333; font-size: 12px; padding-left: 8px; font-weight: bold;"
        )
        layout.addWidget(self.project_label)

        self.attachment_label = QLabel("No file attached.")
        self.attachment_label.setStyleSheet(
            "color: #666; font-size: 12px; padding-left: 8px;"
        )
        layout.addWidget(self.attachment_label)

        self.chat_view = QPlainTextEdit()
        self.chat_view.setReadOnly(True)
        self.chat_view.setStyleSheet(
            "font-family: Consolas, Menlo, monospace; font-size: 13px;"
        )
        layout.addWidget(self.chat_view, 1)

        bottom = QHBoxLayout()

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Coding & Learning", AssistantMode.CODING.value)
        self.mode_combo.addItem("Strategic Checkpoint", AssistantMode.STRATEGIC.value)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type your message…")

        self.help_button = QPushButton("Help Me")
        self.help_button.setToolTip(
            "Capture the current editor file or pick one manually"
        )
        self.send_button = QPushButton("Send")
        self.clear_button = QPushButton("Clear")

        bottom.addWidget(self.mode_combo)
        bottom.addWidget(self.input, 1)
        bottom.addWidget(self.help_button)
        bottom.addWidget(self.send_button)
        bottom.addWidget(self.clear_button)
        layout.addLayout(bottom)

        self.send_button.clicked.connect(self._on_send)
        self.input.returnPressed.connect(self._on_send)
        self.clear_button.clicked.connect(self._on_clear)
        self.help_button.clicked.connect(self._trigger_copilot)

    def _setup_thread(self) -> None:
        self.thread = QThread()
        self.worker = AssistantWorker(self.assistant, self.session_id)
        self.worker.moveToThread(self.thread)

        self.send_requested.connect(self.worker.process)
        self.copilot_requested.connect(self.worker.copilot_process)

        self.worker.status_changed.connect(self.status_panel.set_status)
        self.worker.chunk_received.connect(self._append_chunk)
        self.worker.questions_ready.connect(self._show_questions)
        self.worker.finished.connect(self._on_finished)
        self.worker.copilot_finished.connect(self._on_copilot_finished)
        self.worker.write_proposed.connect(self._on_write_proposed)
        self.worker.error.connect(self._on_error)

        self.thread.start()

    def _setup_hotkeys(self) -> None:
        local = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        local.activated.connect(self._trigger_copilot)
        if register_global_hotkey("<ctrl>+<shift>+<space>"):
            on_global_hotkey(self._trigger_copilot)

    # ---------- project menu ----------
    def _refresh_project_menu(self) -> None:
        self._project_menu.clear()
        group = QActionGroup(self)
        group.setExclusive(True)
        current_key = self._path_key(self._primary_root())
        for root in self._all_roots():
            action = QAction(str(root), self)
            action.setCheckable(True)
            action.setChecked(self._path_key(root) == current_key)
            action.triggered.connect(
                lambda _checked=False, r=root: self._switch_project(r)
            )
            group.addAction(action)
            self._project_menu.addAction(action)

        self._project_menu.addSeparator()
        add_action = QAction("Add folder…", self)
        add_action.triggered.connect(self._add_project_folder)
        self._project_menu.addAction(add_action)

        if self._extra_roots:
            remove_action = QAction("Remove added folders…", self)
            remove_action.triggered.connect(self._remove_extra_roots)
            self._project_menu.addAction(remove_action)

        current = self._primary_root()
        name = current.name or str(current)
        self.project_label.setText(f"Active project: {name}  ({current})")

    def _path_key(self, p: Path) -> str:
        try:
            return str(p.resolve()).lower()
        except Exception:
            return str(p).lower()

    def _switch_project(self, root: Path) -> None:
        if not root.exists():
            QMessageBox.warning(self, "Project unavailable", f"{root} no longer exists.")
            self._refresh_project_menu()
            return
        self._current_root = root
        self._save_current_root()
        self.attachment_label.setText("No file attached.")
        self._current_attachment_path = None
        self._current_related = []
        self.assistant.set_project_outline("")
        self.chat_view.appendPlainText(
            f"\n[project switched] {root}\n"
            f"[project outline cleared — run Snapshot to reload]\n"
        )
        self._refresh_project_menu()
        self._update_title()

    def _add_project_folder(self) -> None:
        start = str(self._primary_root())
        picked = QFileDialog.getExistingDirectory(self, "Pick a project folder", start)
        if not picked:
            return
        p = Path(picked)
        if not p.exists():
            QMessageBox.warning(self, "Not found", f"{p} does not exist.")
            return
        key = self._path_key(p)
        for existing in self._all_roots():
            if self._path_key(existing) == key:
                QMessageBox.information(self, "Already configured", f"{p} is already a project.")
                return
        self._extra_roots.append(p)
        self._save_extra_roots()
        self._switch_project(p)
        self.chat_view.appendPlainText(f"[project added] {p}\n")

    def _remove_extra_roots(self) -> None:
        if not self._extra_roots:
            return
        msg = (
            "Remove all user-added project folders?\n\n"
            "Configured roots from config.py are never removed.\n\n"
            + "\n".join(f"  - {p}" for p in self._extra_roots)
        )
        reply = QMessageBox.question(
            self, "Remove added folders", msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._extra_roots = []
        self._save_extra_roots()
        if self._path_key(self._current_root) not in {
            self._path_key(r) for r in self._all_roots()
        }:
            self._current_root = self._primary_default_root()
            self._save_current_root()
        self._refresh_project_menu()
        self._update_title()
        self.chat_view.appendPlainText("[added folders removed]\n")

    def _update_title(self) -> None:
        name = self._primary_root().name or str(self._primary_root())
        self.setWindowTitle(f"Offline Dual-Memory Assistant — {name}")

    # ---------- send ----------
    def _on_send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        mode_value = self.mode_combo.currentData()
        self.chat_view.appendPlainText(f"\nYou: {text}")
        self.chat_view.appendPlainText("Assistant: ")
        self.input.clear()
        self.send_button.setEnabled(False)
        self.send_requested.emit(text, mode_value)

    def _append_chunk(self, chunk: str) -> None:
        cursor = self.chat_view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(chunk)
        self.chat_view.setTextCursor(cursor)
        self.chat_view.ensureCursorVisible()

    def _show_questions(self, questions: list[str]) -> None:
        pass

    def _on_finished(self, full_response: str) -> None:
        self.chat_view.appendPlainText("\n")
        self.send_button.setEnabled(True)
        self.input.setFocus()

    def _on_error(self, message: str) -> None:
        self.chat_view.appendPlainText(f"\n[ERROR] {message}\n")
        self.send_button.setEnabled(True)
        self.help_button.setEnabled(True)
        self.input.setFocus()

    def _on_clear(self) -> None:
        self.assistant.clear_session(self.session_id)
        self.chat_view.clear()
        self.chat_view.appendPlainText("(session cleared)\n")

    # ---------- profile ----------
    def _open_onboarding(self) -> None:
        dlg = OnboardingDialog(self.assistant.profile_store.load(), self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.assistant.profile_store.update(dlg.to_profile_patch())
            self.chat_view.appendPlainText("\n[profile updated]\n")

    def _show_profile(self) -> None:
        summary = self.assistant.profile_store.natural_summary()
        self.chat_view.appendPlainText(
            "\n--- Current Tier-2 Profile ---\n" + summary + "\n--- end ---\n"
        )

    # ---------- snapshot ----------
    def _snapshot_project(self) -> None:
        primary = self._primary_root()
        self.chat_view.appendPlainText(f"\n[snapshot] scanning {primary} ...\n")
        try:
            outline = build_outline(
                primary, max_files=self.assistant.config.max_project_outline_files
            )
        except Exception as exc:
            self.chat_view.appendPlainText(
                f"\n[snapshot failed] {type(exc).__name__}: {exc}\n"
            )
            return
        self.assistant.set_project_outline(outline)
        self.assistant.chat_db.add_message(
            self.session_id, "user", "[snapshot requested]", "coding"
        )
        self.assistant.chat_db.add_message(
            self.session_id, "assistant", outline, "coding"
        )
        self.chat_view.appendPlainText(outline + "\n")
        self.chat_view.appendPlainText(
            "[project outline stored — every reply now has access to it]\n"
        )

    # ---------- copilot ----------
    def _trigger_copilot(self) -> None:
        roots = self._all_roots()
        ctx = capture_editor_context(roots)
        if not ctx.is_ready:
            self._run_copilot_with_picker()
            return
        self._run_copilot_with_context(ctx)

    def _trigger_copilot_pick(self) -> None:
        self._run_copilot_with_picker()

    def _run_copilot_with_picker(self) -> None:
        start_dir = str(self._primary_root())
        picked, _ = QFileDialog.getOpenFileName(
            self, "Pick a file to analyze", start_dir,
            "Code files (*.py *.js *.ts *.html *.css *.md *.txt *.json *.toml *.yaml *.yml);;All files (*)",
        )
        if not picked:
            return
        p = Path(picked)
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "Cannot read file", str(exc))
            return
        ctx = EditorContext(
            window_title="(manual pick)", file_path=p, file_content=content
        )
        self._run_copilot_with_context(ctx)

    def _run_copilot_with_context(self, ctx: EditorContext) -> None:
        self._current_attachment_path = str(ctx.file_path)
        scan_root = self._root_for_file(ctx.file_path)

        question, ok = QInputDialog.getText(
            self,
            "Copilot — ask about this file",
            f"File: {ctx.file_path.name}\n\n"
            "Tip: mention other files by name (e.g. 'trace worker.py')\n"
            "and they will be loaded automatically.\n\n"
            "What do you need help with?",
            QLineEdit.EchoMode.Normal,
            "",
        )
        if not ok or not question.strip():
            self.attachment_label.setText("No file attached.")
            self._current_attachment_path = None
            self._current_related = []
            return

        mentioned_names = self._extract_mentioned_filenames(question)
        mentioned_paths: list[Path] = []
        for name in mentioned_names:
            p = self._find_file_by_name(name, scan_root)
            if p is not None:
                mentioned_paths.append(p)

        # If the question is a symbol query, find the file that defines it
        # and add it to the context — even if the user didn't mention it.
        symbol = self.assistant._extract_symbol_name(question)
        if symbol:
            for sp in find_files_defining_symbol(scan_root, symbol):
                if sp not in mentioned_paths:
                    mentioned_paths.append(sp)

        related_paths = find_related_files(
            ctx.file_path, scan_root,
            max_files=self.assistant.config.max_related_files,
        )

        final_paths: list[Path] = []
        seen: set[Path] = {ctx.file_path}
        for p in mentioned_paths + related_paths:
            if p in seen:
                continue
            seen.add(p)
            final_paths.append(p)

        self._current_related = []
        for p in final_paths:
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                rel = str(p.relative_to(scan_root))
            except ValueError:
                rel = str(p)
            self._current_related.append((rel, content))

        n_rel = len(self._current_related)
        n_mentioned = len([p for p in mentioned_paths if p != ctx.file_path])

        label = f"Attached: {ctx.file_path.name}"
        if n_mentioned:
            label += f"  + {n_mentioned} mentioned"
        if n_rel:
            label += f"  + {n_rel} total context"
        label += f"  —  {ctx.file_path}"
        self.attachment_label.setText(label)

        clipboard_text = ""
        if self.assistant.config.copilot_include_clipboard_default:
            clipboard_text = get_clipboard_text()

        if mentioned_names:
            focus_line = f"[FOCUS ON FILES: {', '.join(mentioned_names)}]\n\n"
            question_for_llm = focus_line + question
        else:
            question_for_llm = question

        self.chat_view.appendPlainText(
            f"\n[Copilot] File: {ctx.file_path.name}"
            + (f"  (+{n_rel} context)" if n_rel else "")
            + f"\nYou: {question}"
        )
        if self._current_related:
            names = ", ".join(rel for rel, _ in self._current_related)
            self.chat_view.appendPlainText(f"[loaded: {names}]")
        self.chat_view.appendPlainText("Assistant: ")

        self.send_button.setEnabled(False)
        self.help_button.setEnabled(False)

        self.copilot_requested.emit(
            question_for_llm,
            str(ctx.file_path),
            ctx.file_content or "",
            clipboard_text,
            self._current_related,
        )

    def _extract_mentioned_filenames(self, text: str) -> list[str]:
        if not text:
            return []
        found: list[str] = []
        seen: set[str] = set()
        for m in _FILENAME_RE.finditer(text):
            name = m.group(1).replace("\\", "/").strip()
            key = name.lower()
            if key in seen:
                continue
            seen.add(key)
            found.append(name)
        return found

    def _find_file_by_name(self, name: str, root: Path) -> Path | None:
        name = name.replace("\\", "/").strip()
        if not name:
            return None
        try:
            direct = (root / name).resolve()
            direct.relative_to(root.resolve())
            if direct.is_file():
                return direct
        except (ValueError, OSError):
            pass
        basename = Path(name).name
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
                if basename in filenames:
                    return Path(dirpath) / basename
        except OSError:
            return None
        return None

    def _on_copilot_finished(self, full_response: str) -> None:
        self.chat_view.appendPlainText("\n")
        self.send_button.setEnabled(True)
        self.help_button.setEnabled(True)
        self.input.setFocus()

        code_blocks = extract_code_blocks(full_response)
        if not code_blocks:
            return
        dlg = CodeApprovalDialog(code_blocks, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.choice:
            set_clipboard_text(dlg.selected_code())
            self.chat_view.appendPlainText(
                "\n[code copied to clipboard — paste into your editor]\n"
            )

    # ---------- file writes ----------
    def _on_write_proposed(self, requests: list) -> None:
        for req in requests:
            resolved = resolve_write_path(req.raw_path, self._all_roots())
            if resolved is None:
                self.chat_view.appendPlainText(
                    f"\n[write rejected] path outside configured project roots: "
                    f"{req.raw_path}\n"
                )
                continue
            old_content = ""
            if resolved.exists():
                try:
                    old_content = resolved.read_text(encoding="utf-8")
                except OSError as exc:
                    self.chat_view.appendPlainText(
                        f"\n[write rejected] cannot read {resolved}: {exc}\n"
                    )
                    continue
            dlg = FileWriteApprovalDialog(
                target_path=resolved, old_content=old_content,
                new_content=req.content, parent=self,
            )
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.approved:
                try:
                    apply_write(resolved, dlg.new_content())
                    self.chat_view.appendPlainText(f"\n[file written] {resolved}\n")
                except OSError as exc:
                    self.chat_view.appendPlainText(
                        f"\n[write failed] {resolved}: {exc}\n"
                    )
            else:
                self.chat_view.appendPlainText(
                    f"\n[write rejected by user] {resolved}\n"
                )

    # ---------- shutdown ----------
    def closeEvent(self, event) -> None:
        self.thread.quit()
        self.thread.wait(3000)
        event.accept()