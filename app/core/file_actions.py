import difflib
import re
from dataclasses import dataclass
from pathlib import Path


_WRITE_BLOCK_RE = re.compile(
    r'<write\s+path="(?P<path>[^"]+)">\s*\n?(?P<content>.*?)\n?\s*</write>',
    re.DOTALL,
)


@dataclass
class WriteRequest:
    raw_path: str
    content: str
    resolved_path: Path | None = None
    error: str | None = None

    @property
    def is_resolved(self) -> bool:
        return self.resolved_path is not None and self.error is None


def extract_write_requests(text: str) -> list[WriteRequest]:
    requests: list[WriteRequest] = []
    for m in _WRITE_BLOCK_RE.finditer(text):
        raw = m.group("path").strip()
        content = m.group("content")
        requests.append(WriteRequest(raw_path=raw, content=content))
    return requests


def resolve_write_path(raw_path: str, roots: list[Path]) -> Path | None:
    if not raw_path:
        return None

    cleaned_roots = [r.resolve() for r in roots if r.exists()]

    p = Path(raw_path)
    if p.is_absolute():
        candidate = p.resolve()
        for root in cleaned_roots:
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        return None

    for root in cleaned_roots:
        candidate = (root / raw_path).resolve()
        try:
            candidate.relative_to(root)
            return candidate
        except ValueError:
            continue
    return None


def compute_diff(old_content: str, new_content: str, label: str) -> str:
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
        n=3,
    )
    return "".join(diff)


def apply_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")