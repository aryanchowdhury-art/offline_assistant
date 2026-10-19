import ast
import os
from dataclasses import dataclass, field
from pathlib import Path


_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".idea", ".vscode", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "site-packages",
}

_TEXT_EXTS = {
    ".py", ".pyw", ".txt", ".md", ".rst", ".toml", ".cfg", ".ini",
    ".json", ".yaml", ".yml", ".html", ".css", ".js", ".ts",
    ".jsx", ".tsx", ".c", ".h", ".cpp", ".hpp", ".java",
    ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1",
}

_MAX_FILE_READ = 200_000


@dataclass
class FileInfo:
    path: Path
    rel_path: str
    size: int
    is_python: bool


@dataclass
class ProjectSnapshot:
    root: Path
    files: list[FileInfo] = field(default_factory=list)
    outline: str = ""


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_EXTS


def _walk_project(root: Path, max_depth: int = 8) -> list[FileInfo]:
    root = root.resolve()
    root_depth = len(root.parts)
    found: list[FileInfo] = []

    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - root_depth
        if depth >= max_depth:
            dirnames.clear()
            continue

        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS and not d.startswith(".")
        ]

        for name in filenames:
            p = Path(dirpath) / name
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size == 0 or size > _MAX_FILE_READ:
                continue
            if not _is_text(p):
                continue
            try:
                rel = str(p.relative_to(root))
            except ValueError:
                rel = str(p)
            found.append(
                FileInfo(
                    path=p,
                    rel_path=rel,
                    size=size,
                    is_python=p.suffix.lower() == ".py",
                )
            )
    return found


def _extract_python_imports(path: Path) -> set[str]:
    names: set[str] = set()
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return names

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def _module_name_for(rel_path: str) -> str:
    p = rel_path.replace("\\", "/")
    if p.endswith(".py"):
        p = p[:-3]
    if p.endswith("/__init__"):
        p = p[: -len("/__init__")]
    return p.replace("/", ".")


def _module_name_to_relative(
    module_name: str, files_by_module: dict[str, FileInfo]
) -> list[FileInfo]:
    hits: list[FileInfo] = []
    if module_name in files_by_module:
        hits.append(files_by_module[module_name])
    prefix = module_name + "."
    for mod, info in files_by_module.items():
        if mod.startswith(prefix) and info not in hits:
            hits.append(info)
    return hits


def find_related_files(
    target: Path,
    root: Path,
    max_files: int = 10,
) -> list[Path]:
    root = root.resolve()
    target = target.resolve()

    all_files = _walk_project(root)

    files_by_module: dict[str, FileInfo] = {}
    for info in all_files:
        if info.is_python:
            mod = _module_name_for(info.rel_path)
            files_by_module[mod] = info

    related: list[Path] = [target]
    seen: set[Path] = {target}

    if target.suffix.lower() != ".py" or not target.exists():
        return related

    imports = _extract_python_imports(target)
    for mod_name in imports:
        matches = _module_name_to_relative(mod_name, files_by_module)
        for info in matches:
            if info.path in seen:
                continue
            seen.add(info.path)
            related.append(info.path)
            if len(related) >= max_files:
                return related

    return related


def find_files_defining_symbol(root: Path, symbol: str) -> list[Path]:
    """
    Walk every .py file in the project and return paths that define a
    function, method, or class named `symbol`.
    """
    if not symbol or not symbol.isidentifier():
        return []

    root = root.resolve()
    hits: list[Path] = []

    for info in _walk_project(root):
        if not info.is_python:
            continue
        try:
            source = info.path.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue

        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ):
                if node.name == symbol:
                    hits.append(info.path)
                    break

    return hits


def build_outline(root: Path, max_files: int = 200) -> str:
    root = root.resolve()
    if not root.exists():
        return f"(project root not found: {root})"

    files = _walk_project(root)[:max_files]

    tree: dict[str, list[FileInfo]] = {}
    for info in files:
        parts = info.rel_path.split(os.sep)
        top = parts[0] if len(parts) > 1 else "(root)"
        tree.setdefault(top, []).append(info)

    lines = [f"PROJECT OUTLINE — {root}"]
    lines.append(f"Total files tracked: {len(files)}")
    lines.append("")

    for folder in sorted(tree.keys()):
        lines.append(f"[{folder}]")
        for info in sorted(tree[folder], key=lambda i: i.rel_path):
            lines.append(f"  {info.rel_path}  ({info.size} bytes)")
        lines.append("")

    return "\n".join(lines)