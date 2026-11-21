"""Tasks live in tasks/<id>/: task.json (instruction, check), optional files/ copied into /workspace."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Task:
    id: str
    instruction: str
    check: str  # shell command run in the sandbox after the agent; exit 0 = pass
    files_dir: Path | None = None
    max_steps: int = 30


def load_task(path: str | Path) -> Task:
    path = Path(path)
    spec = json.loads((path / "task.json").read_text())
    files = path / "files"
    return Task(
        id=spec.get("id", path.name),
        instruction=spec["instruction"],
        check=spec["check"],
        files_dir=files if files.is_dir() else None,
        max_steps=spec.get("max_steps", 30),
    )
