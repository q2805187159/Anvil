from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from .contracts import MemoryState


class FileMemoryStore:
    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, MemoryState]] = {}
        self._lock = Lock()

    def load(self, namespace: str) -> MemoryState:
        path = self._path_for_namespace(namespace)
        if not path.exists():
            return MemoryState(namespace=namespace)

        mtime = path.stat().st_mtime
        cached = self._cache.get(namespace)
        if cached and cached[0] == mtime:
            return cached[1].model_copy(deep=True)

        state = MemoryState.model_validate_json(path.read_text(encoding="utf-8"))
        self._cache[namespace] = (mtime, state)
        return state.model_copy(deep=True)

    def save(self, namespace: str, memory_state: MemoryState) -> None:
        path = self._path_for_namespace(namespace)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp_path.write_text(memory_state.model_dump_json(indent=2), encoding="utf-8")
            tmp_path.replace(path)
            self._cache[namespace] = (path.stat().st_mtime, memory_state.model_copy(deep=True))

    def invalidate(self, namespace: str) -> None:
        self._cache.pop(namespace, None)

    def list_namespaces(self) -> list[str]:
        namespaces: list[str] = []
        if not self.base_path.exists():
            return namespaces
        for path in self.base_path.glob("*.json"):
            namespaces.append(path.stem.replace("__", "/"))
        return sorted(namespaces)

    def _path_for_namespace(self, namespace: str) -> Path:
        safe = namespace.replace("/", "__")
        return self.base_path / f"{safe}.json"
