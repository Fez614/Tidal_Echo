from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class LocalMemoryProvider:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _ensure_file(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def list(self) -> list[dict[str, Any]]:
        self._ensure_file()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "[]")
        except json.JSONDecodeError:
            data = []
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict)]

    def write_all(self, items: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

