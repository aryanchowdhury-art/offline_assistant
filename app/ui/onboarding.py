from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)


class OnboardingDialog(QDialog):
    """First-run dialog that populates the Tier-2 profile."""

    def __init__(self, profile: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome — first-time setup")
        self.resize(580, 520)

        layout = QVBoxLayout(self)

        intro = QLabel(
            "Before we start, I need to learn a few things about you.\n"
            "Everything stays on this machine and is stored in your Tier-2 profile."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        identity = profile.get("identity") or {}
        prefs = profile.get("preferences") or {}

        self.name_edit = QLineEdit(identity.get("name", ""))
        self.role_edit = QLineEdit(identity.get("role", ""))

        self.tone_combo = QComboBox()
        self.tone_combo.addItems(["direct", "warm", "neutral", "dry-humored"])
        self.tone_combo.setEditable(True)
        self.tone_combo.setCurrentText(prefs.get("tone", "direct"))

        self.length_combo = QComboBox()
        self.length_combo.addItems(["concise", "balanced", "thorough"])
        self.length_combo.setEditable(True)
        self.length_combo.setCurrentText(prefs.get("length", "concise"))

        self.format_combo = QComboBox()
        self.format_combo.addItems(["code-first", "prose-first", "bullets"])
        self.format_combo.setEditable(True)
        self.format_combo.setCurrentText(prefs.get("format", "code-first"))

        self.rules_edit = QPlainTextEdit(
            "\n".join(profile.get("operational_rules") or [])
        )
        self.rules_edit.setPlaceholderText(
            "One rule per line, e.g.\n"
            "Always offline\n"
            "Ask before assuming hardware limits"
        )
        self.rules_edit.setFixedHeight(90)

        self.guidelines_edit = QPlainTextEdit(
            "\n".join(profile.get("core_guidelines") or [])
        )
        self.guidelines_edit.setPlaceholderText(
            "One guideline per line, e.g.\n"
            "Prefer minimal runnable snippets"
        )
        self.guidelines_edit.setFixedHeight(90)

        form.addRow("What should I call you?", self.name_edit)
        form.addRow("Your primary role / focus", self.role_edit)
        form.addRow("Preferred tone", self.tone_combo)
        form.addRow("Preferred length", self.length_combo)
        form.addRow("Preferred format", self.format_combo)
        form.addRow("Operational rules", self.rules_edit)
        form.addRow("Core guidelines", self.guidelines_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def to_profile_patch(self) -> dict:
        return {
            "identity": {
                "name": self.name_edit.text().strip(),
                "role": self.role_edit.text().strip(),
            },
            "preferences": {
                "tone": self.tone_combo.currentText().strip(),
                "length": self.length_combo.currentText().strip(),
                "format": self.format_combo.currentText().strip(),
            },
            "operational_rules": [
                line.strip()
                for line in self.rules_edit.toPlainText().splitlines()
                if line.strip()
            ],
            "core_guidelines": [
                line.strip()
                for line in self.guidelines_edit.toPlainText().splitlines()
                if line.strip()
            ],
        }