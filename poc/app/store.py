from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from threading import Lock
from typing import Any, Optional

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
GALLERY_PATH = DATA_DIR / "gallery.json"


class GalleryStore:
    """
    Edge cache stand-in: templates + per-gate policy.
    revoke() models a priority delta from central SoT.
    """

    def __init__(self, path: Path = GALLERY_PATH) -> None:
        self._path = path
        self._lock = Lock()
        self._data: Optional[dict[str, Any]] = None
        self.policy_version = 1
        self.cache_age_minutes_override: Optional[float] = None

    def reset(self) -> None:
        with self._lock:
            self._data = None
            self.policy_version = 1
            self.cache_age_minutes_override = None

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            with self._path.open(encoding="utf-8") as f:
                self._data = json.load(f)
        return self._data

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._load())

    def employees(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._load()["employees"])

    def default_cache_age(self) -> float:
        with self._lock:
            if self.cache_age_minutes_override is not None:
                return self.cache_age_minutes_override
            return float(self._load().get("cache_age_minutes_default", 5))

    def revoke(self, employee_id: str) -> bool:
        """Remove template from local cache and bump policy version."""
        with self._lock:
            data = self._load()
            before = len(data["employees"])
            data["employees"] = [
                e for e in data["employees"] if e["employee_id"] != employee_id
            ]
            removed = len(data["employees"]) < before
            if removed:
                self.policy_version += 1
            return removed

    def set_fresh_cache(self, age_minutes: float = 1.0) -> None:
        with self._lock:
            self.cache_age_minutes_override = age_minutes


gallery_store = GalleryStore()


class GuardQueue:
    """Events waiting for human review — prod would be a real console + DB."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def reset(self) -> None:
        with self._lock:
            self._items.clear()

    def enqueue(self, event_id: str, payload: dict[str, Any]) -> None:
        with self._lock:
            self._items[event_id] = payload

    def list_open(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(v) for v in self._items.values() if v.get("status") == "open"]

    def resolve(
        self, event_id: str, *, action: str, operator_id: str
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            item = self._items.get(event_id)
            if item is None or item.get("status") != "open":
                return None
            item["status"] = "resolved"
            item["operator_action"] = action
            item["operator_id"] = operator_id
            return deepcopy(item)


guard_queue = GuardQueue()
