import json
import re
from collections.abc import Iterator

from app.config import AppConfig
from app.core.auditor import ProfileAuditor
from app.core.types import AssistantMode, AuditResult
from app.llm.engine import LlamaEngine
from app.llm.prompts import CODING_SYSTEM, STRATEGIC_SYSTEM
from app.memory.chat_db import ChatDB
from app.memory.profile_store import ProfileStore


_MEMORY_QUERY_PATTERNS = [
    re.compile(r"^\s*what\s+(do\s+)?(you|u)\s+(know|remember|recall)\s+(about\s+)?(me|myself)\b", re.I),
    re.compile(r"^\s*who\s+am\s+i\b", re.I),
    re.compile(r"^\s*what('?s|\s+is)\s+my\s+(name|profile|role|job)\b", re.I),
    re.compile(r"^\s*(show|give|tell|print|dump|export|send|display)\b.*\b(profile|info|information|details|data|memory)\b.*\b(me|my|about)\b", re.I),
    re.compile(r"^\s*(show|give|tell|print|dump|export|send|display)\s+(me\s+)?my\s+(profile|info|information|details|data|memory)\b", re.I),
    re.compile(r"^\s*summar(y|ise|ize)\b.*\b(profile|me|your\s+memory)\b", re.I),
    re.compile(r"^\s*what\s+have\s+you\s+learned\s+about\s+me\b", re.I),
    re.compile(r"^\s*tell\s+me\s+(what\s+you\s+know\s+)?about\s+(me|myself|my\s+profile)\b", re.I),
    re.compile(r"^\s*your\s+memory\s+(of|about)\s+me\b", re.I),
    re.compile(r"^\s*my\s+(profile|info|information|details|data)\s*[?.!]?\s*$", re.I),
    re.compile(r"^\s*(show|give|tell|print|dump|export|send|display)\b.*\b(profile|info|my)\b.*\b(json|yaml|format)\b", re.I),
    re.compile(r"^\s*my\s+(profile|info)\s+(as|in)\s+(json|yaml|text|format)\b", re.I),
]

_JSON_HINT_RE = re.compile(r"\b(json|as\s+json|in\s+json)\b", re.I)


_SYMBOL_REQUEST_PATTERNS = [
    re.compile(
        r"\b(?:show|give|paste|print|copy|display|get|find|locate)\b[^.?!]*?"
        r"\b(?:signature|definition|declaration|code|body|implementation|source)\b"
        r"[^.?!]*?\b(?:of|for)\s+(?:the\s+)?(?:(?:function|method|class)\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)",
        re.I,
    ),
    re.compile(
        r"\b(?:signature|definition|declaration)\s+(?:of|for)\s+"
        r"(?:the\s+)?(?:(?:function|method|class)\s+)?"
        r"([A-Za-z_][A-Za-z0-9_]*)",
        re.I,
    ),
    re.compile(
        r"\b(?:show|give|paste|print|copy|display|get|find|locate)\b[^.?!]*?"
        r"\b(?:function|method|class)\s+([A-Za-z_][A-Za-z0-9_]*)",
        re.I,
    ),
    re.compile(r"\bdef\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.I),
    re.compile(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b", re.I),
]

_FULL_BODY_HINTS = re.compile(r"\b(body|full|entire|complete|whole|implementation)\b", re.I)
_SIGNATURE_HINTS = re.compile(r"\bsignature\b", re.I)


_INTERRUPT_STARTERS = re.compile(
    r"^\s*(?:"
    r"can|could|would|should|will|shall|"
    r"what|how|why|when|where|who|which|"
    r"write|show|give|tell|explain|make|create|build|"
    r"fix|debug|refactor|review|generate|list|draft|"
    r"sketch|design|help|run|install|open|close|"
    r"stop|start|add|remove|delete|update|change|"
    r"modify|test"
    r")\b",
    re.I,
)


_CODE_BLOCK_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)
_MEMORY_QUERY_MAX_LEN = 200


def extract_code_blocks(text: str) -> list[str]:
    return [m.group(1).rstrip() for m in _CODE_BLOCK_RE.finditer(text)]


def _find_symbol_in_source(content: str, symbol: str):
    import ast as _ast
    try:
        tree = _ast.parse(content)
    except SyntaxError:
        return None
    for node in _ast.walk(tree):
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
            if node.name == symbol:
                start = node.lineno - 1
                end = getattr(node, "end_lineno", None)
                if end is None:
                    continue
                return (start, end)
    return None


def _extract_signature_lines(func_lines: list[str]) -> str:
    collected: list[str] = []
    depth = 0
    for line in func_lines:
        collected.append(line)
        depth += line.count("(") - line.count(")")
        depth += line.count("[") - line.count("]")
        depth += line.count("{") - line.count("}")
        if depth <= 0 and line.rstrip().endswith(":"):
            break
    return "\n".join(collected)


class OfflineAssistant:
    def __init__(
        self,
        config: AppConfig,
        chat_db: ChatDB,
        profile_store: ProfileStore,
        engine: LlamaEngine,
        auditor: ProfileAuditor,
    ):
        self.config = config
        self.chat_db = chat_db
        self.profile_store = profile_store
        self.engine = engine
        self.auditor = auditor
        self.project_outline: str = ""
        self.last_copilot_path: str = ""
        self.last_copilot_content: str = ""

    # ---------- memory intercept ----------
    def is_memory_query(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if len(t) > _MEMORY_QUERY_MAX_LEN:
            return False
        return any(p.search(t) for p in _MEMORY_QUERY_PATTERNS)

    def memory_answer(self, query: str = "") -> str:
        profile = self.profile_store.load()
        if _JSON_HINT_RE.search(query):
            raw = json.dumps(profile, ensure_ascii=False, indent=2)
            return (
                "Here is your REAL user_profile.json — the exact file on disk, "
                "nothing invented:\n\n"
                "```json\n"
                f"{raw}\n"
                "```"
            )
        summary = self.profile_store.natural_summary()
        return (
            "Here is what my persistent Tier-2 memory contains about you.\n"
            "This is the REAL data from your user_profile.json — nothing here "
            "is invented.\n\n" + summary
        )

    # ---------- symbol extraction ----------
    @staticmethod
    def _extract_symbol_name(question: str) -> str | None:
        if not question:
            return None
        for pat in _SYMBOL_REQUEST_PATTERNS:
            m = pat.search(question)
            if m:
                return m.group(1)
        return None

    def try_extract_symbol(self, question: str, file_content: str):
        """Legacy single-file entry point."""
        return self.try_extract_symbol_multi(
            question, [("(active)", file_content)]
        )

    def try_extract_symbol_multi(
        self,
        question: str,
        files: list[tuple[str, str]],
    ):
        """Search every loaded file for the requested symbol."""
        symbol = self._extract_symbol_name(question)
        if not symbol:
            return None

        for rel_path, content in files:
            loc = _find_symbol_in_source(content, symbol)
            if loc is None:
                continue

            lines = content.splitlines()
            start, end = loc
            snippet_lines = lines[start:end]

            wants_full = bool(_FULL_BODY_HINTS.search(question))
            wants_signature = bool(_SIGNATURE_HINTS.search(question))

            if wants_signature and not wants_full:
                body = _extract_signature_lines(snippet_lines)
                label = f"signature of `{symbol}`"
            else:
                body = "\n".join(snippet_lines)
                label = f"full source of `{symbol}`"

            header = (
                f"Here is the {label}, extracted directly from "
                f"`{rel_path}` (no LLM involved — this is the exact code "
                f"on disk):\n\n"
            )
            fenced = "```python\n" + body + "\n```"
            return symbol, header + fenced

        return None

    def try_answer_symbol_query(
        self, question: str, roots: list
    ):
        """
        Search every configured project root for the symbol named in the
        question. If found, extract it verbatim from the file that defines
        it. If not found, return a deterministic "not found" message.
        Either way, the LLM is bypassed — no hallucination possible.

        Returns (symbol, text) or None if the question isn't a symbol query.
        """
        symbol = self._extract_symbol_name(question)
        if not symbol:
            return None

        # Import inside the method so the assistant module stays import-clean
        # even if the capture package is missing on some deployment.
        try:
            from app.capture.project import find_files_defining_symbol
        except Exception:
            return None

        for root in roots:
            try:
                hits = find_files_defining_symbol(root, symbol)
            except Exception:
                continue

            for path in hits:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                loc = _find_symbol_in_source(content, symbol)
                if loc is None:
                    continue

                lines = content.splitlines()
                start, end = loc
                snippet_lines = lines[start:end]

                wants_full = bool(_FULL_BODY_HINTS.search(question))
                wants_signature = bool(_SIGNATURE_HINTS.search(question))

                if wants_signature and not wants_full:
                    body = _extract_signature_lines(snippet_lines)
                    label = f"signature of `{symbol}`"
                else:
                    body = "\n".join(snippet_lines)
                    label = f"full source of `{symbol}`"

                try:
                    rel = str(path.relative_to(root))
                except ValueError:
                    rel = str(path)

                header = (
                    f"Here is the {label}, extracted directly from "
                    f"`{rel}` (no LLM involved — this is the exact code on "
                    f"disk):\n\n"
                )
                fenced = "```python\n" + body + "\n```"
                return symbol, header + fenced

        # Symbol query, but the symbol doesn't exist anywhere we can see.
        return symbol, (
            f"`{symbol}` is not defined in any `.py` file under the current "
            f"project roots. I searched every file — no match. If you think "
            f"it exists, check the spelling or add its folder to the Project "
            f"menu."
        )

    def set_last_copilot_file(self, path: str, content: str) -> None:
        self.last_copilot_path = path or ""
        self.last_copilot_content = content or ""

    def try_extract_symbol_from_last(self, question: str):
        if not self.last_copilot_content:
            return None
        return self.try_extract_symbol(question, self.last_copilot_content)

    # ---------- project outline ----------
    def set_project_outline(self, outline: str) -> None:
        self.project_outline = outline or ""

    def looks_like_answer(self, text: str) -> bool:
        t = (text or "").strip()
        if not t:
            return False
        if t.endswith("?"):
            return False
        if _INTERRUPT_STARTERS.search(t):
            return False
        return True

    # ---------- context ----------
    def recent_context(self, session_id: str) -> str:
        rows = self.chat_db.get_recent(session_id, self.config.recent_messages)
        if not rows:
            return "(no prior context)"
        return "\n".join(f"{row['role']}: {row['content']}" for row in rows)

    def audit(self, session_id: str, user_input: str) -> AuditResult:
        return self.auditor.audit(user_input, self.recent_context(session_id))

    # ---------- text generation ----------
    def stream_response(
        self, session_id: str, user_input: str, mode: AssistantMode
    ) -> Iterator[str]:
        profile = self.profile_store.load()
        system_prompt = (
            CODING_SYSTEM if mode == AssistantMode.CODING else STRATEGIC_SYSTEM
        )
        memory_block = self._build_memory_block(profile)
        messages: list[dict] = [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": memory_block},
        ]
        for row in self.chat_db.get_recent(session_id, self.config.recent_messages):
            messages.append({"role": row["role"], "content": row["content"]})
        messages.append({"role": "user", "content": user_input})

        temperature = (
            self.config.temperature_coding
            if mode == AssistantMode.CODING
            else self.config.temperature_strategic
        )
        yield from self.engine.stream_chat_completion(
            messages, temperature=temperature, max_tokens=self.config.max_tokens
        )

    # ---------- copilot ----------
    def stream_copilot_response(
        self,
        session_id: str,
        user_question: str,
        file_path: str,
        file_content: str,
        clipboard_text: str | None = None,
        related_files: list[tuple[str, str]] | None = None,
    ) -> Iterator[str]:
        profile = self.profile_store.load()
        memory_block = self._build_memory_block(profile)

        active_budget = self.config.copilot_max_file_chars
        related_budget = max(500, active_budget // 4)
        per_related = (
            max(400, related_budget // max(1, len(related_files)))
            if related_files else 0
        )

        active_block = self._format_active_file(file_path, file_content, active_budget)
        related_block = (
            self._format_related_files(related_files, per_related)
            if related_files else ""
        )
        clipboard_block = ""
        if clipboard_text:
            clipboard_block = (
                "\n\n=== USER'S CLIPBOARD ===\n"
                "```\n" f"{clipboard_text[:1500]}\n" "```"
            )

        preamble = (
            "You are helping the user with files from their project. Below "
            "is the EXACT, CURRENT content of each loaded file. This is the "
            "ground truth.\n\n"
            f"{active_block}{related_block}{clipboard_block}\n\n"
            "STRICT RULES — read carefully:\n"
            "- If the user's question begins with a line like "
            "'[FOCUS ON FILES: x.py, y.py]', you MUST answer about those "
            "files. Do NOT describe the ACTIVE FILE unless it is one of them. "
            "The active file is only a fallback when nothing else is named.\n"
            "- Every file above is labelled with its path. When you refer to "
            "a file, use its real path. Do not mix up the ACTIVE FILE with "
            "the mentioned files.\n"
            "- If the user asks for a function, class, or code excerpt, copy "
            "it VERBATIM from the correct file above. Do not paraphrase. "
            "Do not invent. Do not change the signature.\n"
            "- If the requested code is NOT present in any file above, say "
            "so plainly. Do NOT invent a plausible-looking function.\n"
            "- Never fabricate function names, parameters, or logic.\n"
            "- Answer in the same language as the file."
        )

        messages = [
            {"role": "system", "content": CODING_SYSTEM},
            {"role": "system", "content": memory_block},
            {"role": "system", "content": preamble},
            {"role": "user", "content": user_question},
        ]
        yield from self.engine.stream_chat_completion(
            messages,
            temperature=self.config.temperature_coding,
            max_tokens=self.config.max_tokens,
        )

    def _format_active_file(self, path: str, content: str, budget: int) -> str:
        truncated = self._truncate_text(content, budget)
        return (
            "=== ACTIVE FILE ===\n"
            f"PATH: {path}\n"
            f"SIZE: {len(content)} characters\n"
            "```\n"
            f"{truncated}\n"
            "```\n"
        )

    def _format_related_files(
        self, related: list[tuple[str, str]], per_file_cap: int
    ) -> str:
        if not related:
            return ""
        parts = ["\n=== RELATED FILES (imports + mentioned files) ==="]
        for rel_path, content in related:
            snippet = self._truncate_text(content, per_file_cap)
            parts.append(f"\n--- {rel_path} ---\n```\n{snippet}\n```")
        parts.append("")
        return "\n".join(parts)

    def _truncate_text(self, content: str, limit: int) -> str:
        if len(content) <= limit:
            return content
        head = content[: limit * 2 // 3]
        tail = content[-limit // 3 :]
        return f"{head}\n\n... [truncated {len(content) - limit} chars] ...\n\n{tail}"

    # ---------- memory block ----------
    def _build_memory_block(self, profile: dict) -> str:
        identity = profile.get("identity") or {}
        prefs = profile.get("preferences") or {}
        hw = profile.get("hardware_limits") or {}
        rules = profile.get("operational_rules") or []
        guidelines = profile.get("core_guidelines") or []
        answers = profile.get("probe_answers") or {}

        try:
            raw_json = json.dumps(profile, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            raw_json = "{}"

        answers_block = ""
        if answers:
            lines = [f"- {q.rstrip('?').strip()}: {a.strip()}" for q, a in answers.items()]
            answers_block = (
                "\nLEARNED ABOUT THIS USER (answers to your own probes):\n"
                + "\n".join(lines) + "\n"
            )

        outline_block = ""
        if self.project_outline:
            outline_block = (
                "\nPROJECT OUTLINE (the user's own project — reference it when "
                "they ask about files, structure, or workflow):\n"
                + self.project_outline + "\n"
            )

        return (
            "You maintain a persistent Tier-2 memory of this user. "
            "It is your long-term memory and it is authoritative.\n\n"
            "STRICT RULES — READ CAREFULLY:\n"
            "- The user profile JSON below is the ONLY source of truth about "
            "this user. It is loaded from disk on every turn.\n"
            "- You MUST NOT invent, guess, extrapolate, or fabricate any fact "
            "about the user. No fake names, emails, universities, employers, "
            "projects, dates, references, skills, or personal details. EVER.\n"
            "- If the user asks for information that is NOT present in the "
            "profile below, say: 'That is not in my memory. Would you like to "
            "tell me?' — and nothing else.\n"
            "- If the user asks you to output their profile in JSON or any "
            "structured format, output ONLY the exact JSON below.\n"
            "- If a PROJECT OUTLINE is present below, you can see the user's "
            "entire project file structure. Use it. Never say you cannot "
            "access the outline — it is right here.\n"
            "- Apply the profile silently to every reply: tone, length, "
            "format, depth.\n\n"
            "PROFILE HEADLINE:\n"
            f"- Name: {identity.get('name', 'unknown')}\n"
            f"- Role: {identity.get('role', 'unknown')}\n"
            f"- Tone: {prefs.get('tone', 'n/a')}\n"
            f"- Length: {prefs.get('length', 'n/a')}\n"
            f"- Format: {prefs.get('format', 'n/a')}\n"
            f"- Hardware: {hw.get('cpu', '?')} / {hw.get('ram_gb', '?')} GB / "
            f"{'offline only' if hw.get('offline_only') else 'online allowed'}\n"
            f"- Operational rules active: {len(rules)}\n"
            f"- Core guidelines active: {len(guidelines)}\n"
            + answers_block
            + "\nRAW PROFILE JSON (ground truth — never deviate):\n"
            "```json\n" + raw_json + "\n```\n"
            + outline_block
        )

    # ---------- persistence ----------
    def save_user(self, session_id: str, text: str, mode: AssistantMode) -> None:
        self.chat_db.add_message(session_id, "user", text, mode.value)

    def save_assistant(self, session_id: str, text: str, mode: AssistantMode) -> None:
        self.chat_db.add_message(session_id, "assistant", text, mode.value)

    def save_copilot_turn(
        self, session_id: str, question: str, response: str, file_path: str
    ) -> None:
        self.chat_db.add_message(
            session_id, "user", f"[copilot:{file_path}] {question}", "coding"
        )
        self.chat_db.add_message(session_id, "assistant", response, "coding")

    def clear_session(self, session_id: str) -> None:
        self.chat_db.clear_session(session_id)