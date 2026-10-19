from dataclasses import dataclass, field
from enum import Enum


class AssistantMode(str, Enum):
    CODING = "coding"
    STRATEGIC = "strategic"


class EngineStatus(str, Enum):
    IDLE = "🟢 IDLE"
    READING_MEMORY = "📂 READING_MEMORY"
    AUDITING_PROFILE = "🧠 AUDITING_PROFILE"
    GENERATING_QUESTIONS = "❓ GENERATING_QUESTIONS"
    GENERATING_RESPONSE = "💬 GENERATING_RESPONSE"


@dataclass
class AuditResult:
    needs_clarification: bool = False
    contradictions: list[str] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    raw: str = ""


@dataclass
class AssistantReply:
    text: str
    questions: list[str]
    audit: AuditResult | None
    mode: AssistantMode