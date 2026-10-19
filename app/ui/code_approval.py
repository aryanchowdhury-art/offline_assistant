from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
)

from app.core.file_actions import compute_diff


class CodeApprovalDialog(QDialog):
    """Ask explicit permission before writing code to the clipboard."""

    def __init__(self, code_blocks: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assistant proposed code")
        self.resize(760, 520)
        self._code_blocks = code_blocks

        layout = QVBoxLayout(self)

        header = QLabel(
            f"The assistant produced {len(code_blocks)} code block(s).\n"
            "Review below, then choose how to apply."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        preview = QPlainTextEdit()
        preview.setReadOnly(True)
        preview.setStyleSheet(
            "font-family: Consolas, Menlo, monospace; font-size: 12px;"
        )
        preview.setPlainText(self._joined_code())
        layout.addWidget(preview, 1)

        buttons = QDialogButtonBox()
        copy_all = buttons.addButton(
            "Copy all blocks", QDialogButtonBox.ButtonRole.AcceptRole
        )
        copy_last = buttons.addButton(
            "Copy last block only", QDialogButtonBox.ButtonRole.AcceptRole
        )
        reject = buttons.addButton(
            "Reject", QDialogButtonBox.ButtonRole.RejectRole
        )

        copy_all.clicked.connect(lambda: self._finish("all"))
        copy_last.clicked.connect(lambda: self._finish("last"))
        reject.clicked.connect(self.reject)

        layout.addWidget(buttons)

        self.choice: str | None = None

    def _joined_code(self) -> str:
        parts = []
        for i, block in enumerate(self._code_blocks, 1):
            parts.append(f"# --- block {i} ---\n{block}")
        return "\n\n".join(parts)

    def _finish(self, mode: str) -> None:
        self.choice = mode
        self.accept()

    def selected_code(self) -> str:
        if self.choice == "last" and self._code_blocks:
            return self._code_blocks[-1]
        return "\n\n".join(self._code_blocks)


class FileWriteApprovalDialog(QDialog):
    """Ask explicit permission before writing to disk. Shows a full diff."""

    def __init__(
        self,
        target_path: Path,
        old_content: str,
        new_content: str,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Permission to write file")
        self.resize(920, 620)
        self._target_path = target_path
        self._new_content = new_content
        self.approved = False

        layout = QVBoxLayout(self)

        existed = bool(old_content)
        action = "modify" if existed else "create"

        header = QLabel(
            f"The assistant wants to {action}:\n{target_path}\n\n"
            "Review the diff below. Nothing is written until you click Apply."
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        diff_text = compute_diff(old_content, new_content, target_path.name)

        self.diff_view = QPlainTextEdit()
        self.diff_view.setReadOnly(True)
        self.diff_view.setStyleSheet(
            "font-family: Consolas, Menlo, monospace; font-size: 12px;"
        )
        self.diff_view.setPlainText(diff_text or "(no textual changes)")
        layout.addWidget(self.diff_view, 1)

        buttons = QDialogButtonBox()
        apply_btn = buttons.addButton(
            "Apply (write to disk)", QDialogButtonBox.ButtonRole.AcceptRole
        )
        reject_btn = buttons.addButton(
            "Reject", QDialogButtonBox.ButtonRole.RejectRole
        )
        apply_btn.clicked.connect(self._on_apply)
        reject_btn.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _on_apply(self) -> None:
        self.approved = True
        self.accept()

    def new_content(self) -> str:
        return self._new_content