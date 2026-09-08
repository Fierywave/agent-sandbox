"""file_reader tool — low risk, no approval.

Reads only from the sandboxed demo_docs/ folder next to this file. Any
attempt to escape it (../, absolute paths, symlinks resolving outside)
is rejected — this is what "sandboxed file reader" in the tool registry
actually means, not just a naming convention.
"""

from pathlib import Path

_SANDBOX_ROOT = (Path(__file__).parent.parent / "demo_docs").resolve()


class PathEscapeError(ValueError):
    pass


def _resolve_safe_path(path: str) -> Path:
    candidate = (_SANDBOX_ROOT / path).resolve()
    try:
        candidate.relative_to(_SANDBOX_ROOT)
    except ValueError:
        raise PathEscapeError(
            f"Path '{path}' resolves outside the sandboxed demo_docs folder"
        )
    return candidate


def run(path: str) -> dict:
    """Contract: input_schema={path: str} -> output_schema={content: str}"""
    safe_path = _resolve_safe_path(path)
    if not safe_path.exists() or not safe_path.is_file():
        raise FileNotFoundError(f"'{path}' not found in sandboxed demo_docs folder")
    return {"content": safe_path.read_text(encoding="utf-8")}