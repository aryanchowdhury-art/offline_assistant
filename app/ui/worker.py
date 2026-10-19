from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot

from app.core.assistant import OfflineAssistant
from app.core.file_actions import extract_write_requests
from app.core.types import AssistantMode, EngineStatus


class AssistantWorker(QObject):
    """Runs all LLM work off the UI thread."""

    status_changed = pyqtSignal(str)
    chunk_received = pyqtSignal(str)
    questions_ready = pyqtSignal(list)
    finished = pyqtSignal(str)
    copilot_finished = pyqtSignal(str)
    write_proposed = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, assistant: OfflineAssistant, session_id: str):
        super().__init__()
        self.assistant = assistant
        self.session_id = session_id

    # ---------- helpers ----------
    def _all_roots(self) -> list:
        return list(self.assistant.config.vscode_workspace_roots)

    # ---------- standard chat ----------
    @pyqtSlot(str, str)
    def process(self, user_input: str, mode_value: str) -> None:
        try:
            mode = AssistantMode(mode_value)
        except ValueError:
            self.error.emit(f"Unknown mode: {mode_value}")
            return

        full_response = ""
        try:
            self.status_changed.emit(EngineStatus.READING_MEMORY.value)
            self.assistant.save_user(self.session_id, user_input, mode)

            # ---- 1. Memory intercept ----
            if self.assistant.is_memory_query(user_input):
                self.status_changed.emit(EngineStatus.GENERATING_RESPONSE.value)
                text = self.assistant.memory_answer(user_input)
                self.chunk_received.emit(text)
                self.assistant.save_assistant(self.session_id, text, mode)
                self.finished.emit(text)
                return

            # ---- 2. Project-wide symbol extract ----
            # "Show me X" → search every configured root for X.
            # Found → extract verbatim. Not found → say so. Never calls LLM.
            extracted = self.assistant.try_answer_symbol_query(
                user_input, self._all_roots()
            )
            if extracted is not None:
                _symbol, text = extracted
                self.status_changed.emit(EngineStatus.GENERATING_RESPONSE.value)
                self.chunk_received.emit(text)
                self.assistant.save_assistant(self.session_id, text, mode)
                self.finished.emit(text)
                return

            # ---- 3. Audit + normal response ----
            profile = self.assistant.profile_store.load()
            pending = list(profile.get("pending_questions") or [])
            if pending and self.assistant.looks_like_answer(user_input):
                for q in pending:
                    self.assistant.profile_store.record_answer(q, user_input)
                self.assistant.profile_store.pop_pending_questions()
                pending = []

            self.status_changed.emit(EngineStatus.AUDITING_PROFILE.value)
            audit = self.assistant.audit(self.session_id, user_input)

            if audit.questions:
                self.status_changed.emit(EngineStatus.GENERATING_QUESTIONS.value)
                self.questions_ready.emit(audit.questions)
                parts: list[str] = []
                if audit.contradictions:
                    parts.append("Cross-check flagged contradictions:")
                    for c in audit.contradictions:
                        parts.append(f"  ⚠ {c}")
                    parts.append("")
                parts.append("Before I go further, I need to close these gaps:")
                for q in audit.questions:
                    parts.append(f"- {q}")
                if pending:
                    parts.append("")
                    parts.append(
                        "(You also still have an open question from before: "
                        f"{pending[0]})"
                    )
                text = "\n".join(parts)
                self.chunk_received.emit(text)
                self.assistant.save_assistant(self.session_id, text, mode)
                self.finished.emit(text)
                return

            self.status_changed.emit(EngineStatus.GENERATING_RESPONSE.value)
            for chunk in self.assistant.stream_response(
                self.session_id, user_input, mode
            ):
                full_response += chunk
                self.chunk_received.emit(chunk)

            if pending:
                reminder = f"\n\n(Still open from before: {pending[0]})"
                full_response += reminder
                self.chunk_received.emit(reminder)

            self.assistant.save_assistant(self.session_id, full_response, mode)

            if self.assistant.config.allow_file_writes:
                writes = extract_write_requests(full_response)
                if writes:
                    self.write_proposed.emit(writes)

            self.finished.emit(full_response)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.status_changed.emit(EngineStatus.IDLE.value)

    # ---------- copilot ----------
    @pyqtSlot(str, str, str, str, list)
    def copilot_process(
        self,
        question: str,
        file_path: str,
        file_content: str,
        clipboard_text: str,
        related_files: list,
    ) -> None:
        full_response = ""
        try:
            self.status_changed.emit(EngineStatus.READING_MEMORY.value)

            # Keep a snapshot of the last Copilot file.
            self.assistant.set_last_copilot_file(file_path, file_content)

            # ---- Symbol extraction (project-wide) ----
            extracted = self.assistant.try_answer_symbol_query(
                question, self._all_roots()
            )
            if extracted is not None:
                _symbol, text = extracted
                self.status_changed.emit(EngineStatus.GENERATING_RESPONSE.value)
                self.chunk_received.emit(text)
                self.assistant.save_copilot_turn(
                    self.session_id, question, text, file_path
                )
                self.copilot_finished.emit(text)
                return

            # ---- Normal copilot response ----
            self.status_changed.emit(EngineStatus.GENERATING_RESPONSE.value)
            related = [(rel, content) for rel, content in (related_files or [])]

            for chunk in self.assistant.stream_copilot_response(
                self.session_id,
                question,
                file_path,
                file_content,
                clipboard_text or None,
                related,
            ):
                full_response += chunk
                self.chunk_received.emit(chunk)

            self.assistant.save_copilot_turn(
                self.session_id, question, full_response, file_path
            )

            if self.assistant.config.allow_file_writes:
                writes = extract_write_requests(full_response)
                if writes:
                    self.write_proposed.emit(writes)

            self.copilot_finished.emit(full_response)
        except Exception as exc:
            self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.status_changed.emit(EngineStatus.IDLE.value)