import os
import sys
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


@dataclass
class AppConfig:
    base_dir: Path = field(default_factory=_resolve_base_dir)
    model_dir_name: str = "model"
    model_filename: str = "qwen2.5-coder-3b-instruct-q4_k_m.gguf"

    # ---- llama.cpp runtime ----
    n_ctx: int = 6144
    n_threads: int = field(
        default_factory=lambda: max(2, min(6, os.cpu_count() or 4))
    )
    n_batch: int = 256
    max_tokens: int = 512
    recent_messages: int = 6

    # ---- Sampling ----
    temperature_coding: float = 0.2
    temperature_strategic: float = 0.3
    temperature_audit: float = 0.1
    top_p: float = 0.9
    repeat_penalty: float = 1.1

    # ---- Chat template ----
    force_chat_format: str | None = None

    # ---- Copilot / capture ----
    vscode_workspace_roots: list[Path] = field(default_factory=list)
    copilot_max_file_chars: int = 12000
    copilot_include_clipboard_default: bool = True

    max_related_files: int = 10
    max_project_outline_files: int = 200

    # ---- File writes ----
    allow_file_writes: bool = True

    # ---- Derived ----
    data_dir: Path = field(init=False)
    db_path: Path = field(init=False)
    profile_path: Path = field(init=False)
    model_path: Path = field(init=False)

    def __post_init__(self) -> None:
        self.data_dir = self.base_dir / "data"
        self.db_path = self.data_dir / "chats.db"
        self.profile_path = self.data_dir / "user_profile.json"
        self.model_path = self.base_dir / self.model_dir_name / self.model_filename

        self.data_dir.mkdir(parents=True, exist_ok=True)

        if not self.vscode_workspace_roots:
            self.vscode_workspace_roots = [self.base_dir]