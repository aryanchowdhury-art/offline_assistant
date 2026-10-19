import json

from app.config import AppConfig
from app.core.types import AuditResult
from app.llm.engine import LlamaEngine
from app.llm.prompts import AUDIT_PROMPT
from app.memory.profile_store import ProfileStore


PROBE_DIMENSIONS: list[str] = [
    "current concrete project or problem the user is working on",
    "short-term goals for the next 1-3 months",
    "long-term trajectory or direction",
    "recurring struggles, blockers, or friction",
    "specific tools, libraries, IDEs actually used day to day",
    "decision-making style under pressure (fast-intuitive vs slow-analytical)",
    "communication pet peeves or hard red lines",
    "underlying motivation for current work",
]


_TEMPLATE_QUESTIONS: dict[str, str] = {
    "current concrete project or problem the user is working on":
        "What specific project or problem are you working on right now?",
    "short-term goals for the next 1-3 months":
        "What are your short-term goals for the next 1-3 months?",
    "long-term trajectory or direction":
        "What long-term direction are you steering toward?",
    "recurring struggles, blockers, or friction":
        "What recurring friction or blocker slows you down most often?",
    "specific tools, libraries, IDEs actually used day to day":
        "Which specific tools, libraries, or IDEs do you actually use day to day?",
    "decision-making style under pressure (fast-intuitive vs slow-analytical)":
        "Under pressure, do you lean fast-intuitive or slow-analytical?",
    "communication pet peeves or hard red lines":
        "What communication habits should I absolutely avoid with you?",
    "underlying motivation for current work":
        "What is the underlying reason you are building or learning what you are?",
}


class ProfileAuditor:
    """Cross-examines the Tier 2 profile against the current context."""

    def __init__(
        self,
        engine: LlamaEngine,
        profile_store: ProfileStore,
        config: AppConfig,
    ):
        self.engine = engine
        self.profile_store = profile_store
        self.config = config

    def audit(self, user_input: str, chat_context: str) -> AuditResult:
        profile = self.profile_store.load()
        known_gaps = list(profile.get("known_gaps") or [])
        answers = profile.get("probe_answers") or {}

        pre_questions = self._precheck(profile)
        if pre_questions:
            result = AuditResult(
                needs_clarification=True,
                gaps=["Profile is missing core fields."],
                questions=pre_questions,
            )
            for q in pre_questions:
                self.profile_store.add_known_gap(q)
            self.profile_store.set_pending_questions(pre_questions)
            self.profile_store.set_last_audit(result)
            return result

        remaining = self._remaining_dimensions(known_gaps, answers)

        if not remaining:
            result = self._contradiction_check(
                profile, chat_context, user_input
            )
            self.profile_store.set_last_audit(result)
            return result

        prompt = AUDIT_PROMPT.format(
            profile=json.dumps(profile, ensure_ascii=False, indent=2),
            known_gaps=json.dumps(known_gaps, ensure_ascii=False, indent=2),
            remaining_dimensions="\n".join(f"- {d}" for d in remaining),
            context=chat_context,
            user_input=user_input,
        )

        raw = self.engine.generate(
            prompt,
            temperature=self.config.temperature_audit,
            max_tokens=320,
            stop=["```"],
        )

        result = self._parse(raw)

        if not result.questions and remaining:
            result.questions = [self._template_question(remaining[0])]
            result.gaps = result.gaps or [remaining[0]]

        result.needs_clarification = bool(result.questions)

        if result.questions:
            for q in result.questions:
                self.profile_store.add_known_gap(q)
            self.profile_store.set_pending_questions(result.questions)
            self.profile_store.set_last_audit(result)

        return result

    @staticmethod
    def _precheck(profile: dict) -> list[str]:
        questions: list[str] = []

        if not profile.get("identity"):
            questions.append("What should I call you, and what is your primary role?")
        if not profile.get("preferences"):
            questions.append(
                "What are your top three response preferences: tone, length, format?"
            )
        if not profile.get("operational_rules"):
            questions.append("What rules should I always follow when helping you?")
        if not profile.get("hardware_limits", {}).get("ram_gb"):
            questions.append(
                "Confirm your hardware limits: CPU, RAM, and whether you are offline-only."
            )

        return questions

    @staticmethod
    def _remaining_dimensions(
        known_gaps: list[str], answers: dict
    ) -> list[str]:
        asked_blob = " ".join(known_gaps).lower()
        answered_blob = " ".join(answers.keys()).lower()
        combined = asked_blob + " " + answered_blob

        remaining: list[str] = []
        for dim in PROBE_DIMENSIONS:
            keywords = [w for w in dim.lower().split() if len(w) > 4]
            if any(k in combined for k in keywords):
                continue
            remaining.append(dim)
        return remaining

    @staticmethod
    def _template_question(dimension: str) -> str:
        return _TEMPLATE_QUESTIONS.get(
            dimension, f"Can you tell me more about your {dimension}?"
        )

    @staticmethod
    def _parse(raw: str) -> AuditResult:
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start == -1 or end == -1:
                raise ValueError("No JSON object found")

            data = json.loads(raw[start : end + 1])

            return AuditResult(
                contradictions=[str(x) for x in data.get("contradictions", [])],
                gaps=[str(x) for x in data.get("gaps", [])],
                questions=[
                    str(x) for x in data.get("questions", []) if str(x).strip()
                ],
                raw=raw,
            )
        except Exception:
            return AuditResult(raw=raw)

    def _contradiction_check(
        self, profile: dict, chat_context: str, user_input: str
    ) -> AuditResult:
        prompt = (
            "You are an audit module. All core dimensions about the user are "
            "already known. Only report contradictions between the profile "
            "and the user's current message. If none exist, return empty lists.\n\n"
            "Return ONLY valid JSON:\n"
            '{"contradictions": [], "gaps": [], "questions": []}\n\n'
            "USER_PROFILE:\n"
            + json.dumps(profile, ensure_ascii=False, indent=2)
            + "\n\nCURRENT_CONTEXT:\n"
            + chat_context
            + "\n\nUSER_INPUT:\n"
            + user_input
        )

        raw = self.engine.generate(
            prompt,
            temperature=self.config.temperature_audit,
            max_tokens=200,
            stop=["```"],
        )
        return self._parse(raw)