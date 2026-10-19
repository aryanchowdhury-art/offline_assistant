import copy
import json
import threading
from pathlib import Path

from app.core.types import AuditResult


class ProfileStore:
    """Tier 2 memory: high-level user profile in JSON."""

    DEFAULT: dict = {
        "identity": {},
        "preferences": {},
        "operational_rules": [],
        "hardware_limits": {
            "cpu": "i5",
            "ram_gb": 8,
            "offline_only": True,
        },
        "core_guidelines": [],
        "known_gaps": [],
        "probe_answers": {},
        "pending_questions": [],
        "last_audit": None,
    }

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._ensure_exists()

    def _ensure_exists(self) -> None:
        if not self.path.exists():
            self._write(copy.deepcopy(self.DEFAULT))

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        for key, value in self.DEFAULT.items():
            data.setdefault(key, copy.deepcopy(value))
        return data

    def _write(self, data: dict) -> None:
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> dict:
        with self._lock:
            self._ensure_exists()
            return self._read()

    def save(self, profile: dict) -> None:
        with self._lock:
            self._write(profile)

    def update(self, patch: dict) -> dict:
        with self._lock:
            profile = self._read()
            profile.update(patch)
            self._write(profile)
            return profile

    def add_known_gap(self, gap: str) -> None:
        gap = (gap or "").strip()
        if not gap:
            return
        with self._lock:
            profile = self._read()
            gaps = profile.setdefault("known_gaps", [])
            if gap not in gaps:
                gaps.append(gap)
            self._write(profile)

    def set_last_audit(self, result: AuditResult) -> None:
        with self._lock:
            profile = self._read()
            profile["last_audit"] = {
                "needs_clarification": result.needs_clarification,
                "contradictions": result.contradictions,
                "gaps": result.gaps,
                "questions": result.questions,
                "raw": result.raw[:1000],
            }
            self._write(profile)

    def set_pending_questions(self, questions: list[str]) -> None:
        with self._lock:
            profile = self._read()
            profile["pending_questions"] = [q for q in questions if q.strip()]
            self._write(profile)

    def pop_pending_questions(self) -> list[str]:
        with self._lock:
            profile = self._read()
            pending = list(profile.get("pending_questions") or [])
            profile["pending_questions"] = []
            self._write(profile)
            return pending

    def record_answer(self, question: str, answer: str) -> None:
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not question or not answer:
            return
        with self._lock:
            profile = self._read()
            answers = profile.setdefault("probe_answers", {})
            answers[question] = answer
            self._write(profile)

    def is_complete(self) -> bool:
        p = self.load()
        identity = p.get("identity") or {}
        prefs = p.get("preferences") or {}
        return bool(
            identity.get("name")
            and identity.get("role")
            and prefs.get("tone")
            and p.get("operational_rules")
        )

    def natural_summary(self) -> str:
        p = self.load()
        identity = p.get("identity") or {}
        prefs = p.get("preferences") or {}
        hw = p.get("hardware_limits") or {}

        name = identity.get("name") or "(not set)"
        role = identity.get("role") or "(not set)"

        lines = [
            f"Name: {name}",
            f"Role / focus: {role}",
            f"Preferred tone: {prefs.get('tone', 'n/a')}",
            f"Preferred length: {prefs.get('length', 'n/a')}",
            f"Preferred format: {prefs.get('format', 'n/a')}",
            (
                "Hardware: "
                f"{hw.get('cpu', '?')} / {hw.get('ram_gb', '?')} GB RAM / "
                f"{'offline only' if hw.get('offline_only') else 'online allowed'}"
            ),
        ]

        rules = p.get("operational_rules") or []
        if rules:
            lines.append(f"Operational rules ({len(rules)} active):")
            for r in rules:
                first_line = r.splitlines()[0].strip()
                if first_line and not first_line.startswith("==="):
                    lines.append(f"  - {first_line[:120]}")

        guidelines = p.get("core_guidelines") or []
        if guidelines:
            lines.append(f"Core guidelines ({len(guidelines)} active):")
            for g in guidelines:
                first_line = g.splitlines()[0].strip()
                if first_line and not first_line.startswith("==="):
                    lines.append(f"  - {first_line[:120]}")

        answers = p.get("probe_answers") or {}
        if answers:
            lines.append(f"Learned about you ({len(answers)} answers on file):")
            for question, answer in answers.items():
                q = question.rstrip("?").strip()[:90]
                a = answer.replace("\n", " ").strip()[:180]
                lines.append(f"  - {q}: {a}")

        gaps = p.get("known_gaps") or []
        pending = p.get("pending_questions") or []
        open_gaps = [g for g in gaps if g not in answers]
        if open_gaps and not pending:
            lines.append("Still open (asked, not yet answered):")
            for g in open_gaps:
                lines.append(f"  - {g[:120]}")

        return "\n".join(lines)