import sys
import traceback

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

from app.config import AppConfig
from app.core.assistant import OfflineAssistant
from app.core.auditor import ProfileAuditor
from app.llm.engine import LlamaEngine
from app.memory.chat_db import ChatDB
from app.memory.profile_store import ProfileStore
from app.ui.main_window import MainWindow
from app.ui.onboarding import OnboardingDialog


def build_assistant() -> OfflineAssistant:
    config = AppConfig()

    if not config.model_path.exists():
        raise FileNotFoundError(
            f"GGUF model not found at:\n{config.model_path}\n\n"
            "Place your Qwen2.5-Coder-3B GGUF in the 'model' folder."
        )

    chat_db = ChatDB(config.db_path)
    profile_store = ProfileStore(config.profile_path)
    engine = LlamaEngine(config)
    auditor = ProfileAuditor(engine, profile_store, config)

    return OfflineAssistant(
        config=config,
        chat_db=chat_db,
        profile_store=profile_store,
        engine=engine,
        auditor=auditor,
    )


def main() -> int:
    app = QApplication(sys.argv)

    icon_path = AppConfig().base_dir / "offline_assistant.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        assistant = build_assistant()
    except Exception as exc:
        QMessageBox.critical(
            None,
            "Startup error",
            f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
        )
        return 1

    if not assistant.profile_store.is_complete():
        dlg = OnboardingDialog(assistant.profile_store.load())
        if dlg.exec() == QDialog.DialogCode.Accepted:
            assistant.profile_store.update(dlg.to_profile_patch())

    window = MainWindow(assistant)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())