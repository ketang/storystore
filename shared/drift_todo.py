"""Append-only drift todo helper.

Records code-side story mismatches for later triage by appending date-stamped
Markdown sections to a drift-todo file.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DRIFT_TODO_PATH = "docs/stories/drift-todo.md"

_FILE_HEADER = """\
# Drift Todo

Append-only log of code-side story mismatches detected for later triage.
Do not manually reorder or delete entries — new items are appended at the end.

"""


def append_drift_todo(
    slug: str,
    description: str,
    *,
    metadata: dict[str, Any] | None = None,
    drift_todo_path: str | Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Append a drift-todo entry for *slug*.

    Parameters
    ----------
    slug:
        The story slug that drifted.
    description:
        Human-readable description of the mismatch.
    metadata:
        Optional key-value pairs to include as a JSON block.
    drift_todo_path:
        Override the default path (``docs/stories/drift-todo.md``).
    now:
        Override the current timestamp (useful for testing).

    Returns
    -------
    Path
        The resolved path of the drift-todo file that was written to.
    """
    path = Path(drift_todo_path) if drift_todo_path else Path(DEFAULT_DRIFT_TODO_PATH)
    timestamp = now or datetime.now(timezone.utc)
    date_stamp = timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")

    # Build the entry
    lines: list[str] = []
    lines.append(f"## [{date_stamp}] {slug}\n")
    lines.append(f"\n{description}\n")

    if metadata:
        lines.append(f"\n```json\n{json.dumps(metadata, indent=2)}\n```\n")

    lines.append("\n")
    entry = "".join(lines)

    # Create parent directories if needed
    path.parent.mkdir(parents=True, exist_ok=True)

    if not path.exists():
        path.write_text(_FILE_HEADER + entry)
    else:
        with path.open("a") as f:
            f.write(entry)

    return path
