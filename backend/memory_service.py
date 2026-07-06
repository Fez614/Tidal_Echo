from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from memory_provider_local import LocalMemoryProvider
except ImportError:  # package import when backend is loaded as a module
    from .memory_provider_local import LocalMemoryProvider


DEFAULT_MEMORY_FILE = Path(__file__).resolve().parents[1] / "memory_bank" / "memories.json"

ALLOWED_TYPES = {"preference", "project", "fact", "task", "emotion", "relationship", "style", "rule"}
ALLOWED_SCOPES = {"core", "long_term", "episodic", "style_sample"}
ALLOWED_STATUS = {"active", "archived", "conflicted"}
CATEGORY_MAP = {
    "core relationship": ("relationship", "core", ["relationship"]),
    "核心关系": ("relationship", "core", ["relationship"]),
    "preferences": ("preference", "long_term", ["preference"]),
    "偏好": ("preference", "long_term", ["preference"]),
    "personal facts": ("fact", "long_term", ["fact"]),
    "个人事实": ("fact", "long_term", ["fact"]),
    "emotional notes": ("emotion", "long_term", ["emotion"]),
    "情绪与触发点": ("emotion", "long_term", ["emotion"]),
    "style samples": ("style", "style_sample", ["style"]),
    "回复风格样本": ("style", "style_sample", ["style"]),
    "rules": ("rule", "core", ["rule"]),
    "规则": ("rule", "core", ["rule"]),
    "规则/禁忌": ("rule", "core", ["rule"]),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def memory_file_from_env() -> Path:
    return Path(os.environ.get("MEMORY_BANK_FILE", str(DEFAULT_MEMORY_FILE)))


def clean_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = []
    for item in value:
        tag = str(item or "").strip()
        if tag and tag not in tags:
            tags.append(tag[:40])
    return tags[:20]


class MemoryService:
    def __init__(self, provider: LocalMemoryProvider | None = None):
        self.provider = provider or LocalMemoryProvider(memory_file_from_env())

    def list(self, include_archived: bool = True) -> list[dict[str, Any]]:
        items = [self._normalize(item) for item in self.provider.list()]
        if not include_archived:
            items = [item for item in items if item.get("status") == "active"]
        return sorted(
            items,
            key=lambda m: (
                m.get("status") != "active",
                -int(m.get("importance") or 0),
                m.get("updatedAt") or "",
            ),
        )

    def save(self, data: dict[str, Any]) -> dict[str, Any]:
        items = self.list(include_archived=True)
        item = self._normalize(data)
        for existing in items:
            if (
                existing.get("status") == "active"
                and existing.get("type") == item.get("type")
                and str(existing.get("content") or "").strip() == str(item.get("content") or "").strip()
            ):
                return existing
        item["id"] = item.get("id") or self._new_id(item)
        item["createdAt"] = item.get("createdAt") or now_iso()
        item["updatedAt"] = now_iso()
        items.append(item)
        self.provider.write_all(items)
        return item

    def update(self, memory_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        items = self.list(include_archived=True)
        for i, item in enumerate(items):
            if item.get("id") == memory_id:
                merged = dict(item)
                merged.update(patch)
                merged["id"] = memory_id
                merged["createdAt"] = item.get("createdAt") or now_iso()
                merged["updatedAt"] = now_iso()
                items[i] = self._normalize(merged)
                self.provider.write_all(items)
                return items[i]
        return None

    def archive(self, memory_id: str) -> dict[str, Any] | None:
        return self.update(memory_id, {"status": "archived"})

    def search(self, query: str = "", options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        options = options or {}
        q = str(query or "").strip().lower()
        items = self.list(include_archived=bool(options.get("includeArchived")))
        if q:
            items = [
                item for item in items
                if q in str(item.get("content") or "").lower()
                or any(q in tag.lower() for tag in item.get("tags") or [])
                or q in str(item.get("type") or "").lower()
            ]
        limit = int(options.get("limit") or 20)
        return items[: max(1, min(limit, 100))]

    def build_context(self, query: str = "", limit: int = 12) -> str:
        items = self.list(include_archived=False)
        selected = [
            item for item in items
            if item.get("scope") == "core"
            or item.get("type") == "relationship"
            or int(item.get("importance") or 0) >= 5
        ]
        selected = selected[: max(1, min(limit, 30))]
        if not selected:
            return ""
        lines = [
            "长期记忆使用规则：你可以使用这些记忆理解阿雾，但不要为了证明自己记得而主动展示记忆；只有当前话题相关时才自然使用。",
            "长期记忆：",
        ]
        for item in selected:
            tags = ", ".join(item.get("tags") or [])
            prefix = f"- [{item.get('type')}/{item.get('scope')}/importance={item.get('importance')}]"
            suffix = f" tags={tags}" if tags else ""
            lines.append(f"{prefix} {item.get('content')}{suffix}")
        return "\n".join(lines)

    def import_markdown(self, text: str, source: str = "import") -> list[dict[str, Any]]:
        current_heading = "personal facts"
        created: list[dict[str, Any]] = []
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#"):
                current_heading = line.lstrip("#").strip().lower()
                continue
            if line.startswith(("-", "*", "•")):
                line = line[1:].strip()
            if not line:
                continue
            memory_type, scope, tags = self._category_fields(current_heading)
            before_id = {item.get("id") for item in self.list(include_archived=False)}
            item = self.save({
                "content": line,
                "type": memory_type,
                "scope": scope,
                "tags": tags,
                "importance": 5 if scope == "core" else 3,
                "source": source or "import",
                "status": "active",
            })
            if item.get("id") not in before_id:
                created.append(item)
        return created

    def _new_id(self, item: dict[str, Any]) -> str:
        prefix = str(item.get("type") or "mem")[:3].lower()
        return f"{prefix}-{uuid.uuid4().hex[:10]}"

    def _category_fields(self, heading: str) -> tuple[str, str, list[str]]:
        h = str(heading or "").strip().lower()
        if h in CATEGORY_MAP:
            return CATEGORY_MAP[h]
        if "relationship" in h or "关系" in h:
            return CATEGORY_MAP["core relationship"]
        if "preference" in h or "偏好" in h:
            return CATEGORY_MAP["preferences"]
        if "emotion" in h or "触发" in h or "情绪" in h:
            return CATEGORY_MAP["emotional notes"]
        if "style" in h or "风格" in h:
            return CATEGORY_MAP["style samples"]
        if "rule" in h or "禁忌" in h or "规则" in h:
            return CATEGORY_MAP["rules"]
        return CATEGORY_MAP["personal facts"]

    def _normalize(self, data: dict[str, Any]) -> dict[str, Any]:
        content = str(data.get("content") or "").strip()
        memory_type = str(data.get("type") or "fact").strip()
        scope = str(data.get("scope") or "long_term").strip()
        status = str(data.get("status") or "active").strip()
        importance = data.get("importance", 3)
        try:
            importance = int(importance)
        except (TypeError, ValueError):
            importance = 3
        return {
            "id": str(data.get("id") or "").strip(),
            "content": content[:2000],
            "type": memory_type if memory_type in ALLOWED_TYPES else "fact",
            "scope": scope if scope in ALLOWED_SCOPES else "long_term",
            "tags": clean_tags(data.get("tags")),
            "importance": max(1, min(5, importance)),
            "emotion": data.get("emotion") if isinstance(data.get("emotion"), dict) else {},
            "source": str(data.get("source") or "manual").strip()[:40],
            "status": status if status in ALLOWED_STATUS else "active",
            "evidence": str(data.get("evidence") or "").strip()[:2000],
            "createdAt": str(data.get("createdAt") or "").strip(),
            "updatedAt": str(data.get("updatedAt") or "").strip(),
            "lastAccessedAt": str(data.get("lastAccessedAt") or "").strip(),
        }


def build_memory_context(path: str | Path | None = None, query: str = "") -> str:
    provider = LocalMemoryProvider(path or memory_file_from_env())
    return MemoryService(provider).build_context(query=query)
