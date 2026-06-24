from __future__ import annotations

import json
from pathlib import Path
from threading import Lock

from .compiler import normalize_memory_for_compiled_storage
from .contracts import MemoryState, MemorySummary, utc_now


class FileMemoryStore:
    """Filesystem HCMS store with fail-open reads and atomic writes."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, MemoryState]] = {}
        self._lock = Lock()

    def load(self, namespace: str) -> MemoryState:
        normalized_namespace = _normalize_namespace(namespace)
        path = self._path_for_namespace(normalized_namespace)
        if not path.exists():
            return MemoryState(namespace=normalized_namespace)

        try:
            mtime = path.stat().st_mtime
            cached = self._cache.get(normalized_namespace)
            if cached and cached[0] == mtime:
                return cached[1].model_copy(deep=True)

            payload = json.loads(path.read_text(encoding="utf-8"))
            state = MemoryState.model_validate(payload)
        except Exception:
            state = MemoryState(
                namespace=normalized_namespace,
                summary=MemorySummary(summary="Memory store read failed; HCMS fell back to an empty state."),
            )
        self._cache[normalized_namespace] = (path.stat().st_mtime if path.exists() else 0.0, state)
        return state.model_copy(deep=True)

    def save(self, namespace: str, memory_state: MemoryState) -> None:
        normalized_namespace = _normalize_namespace(namespace)
        state = memory_state.model_copy(deep=True)
        state.namespace = normalized_namespace
        state.memories = [
            normalize_memory_for_compiled_storage(normalized_namespace, memory)
            for memory in state.memories
        ]
        state.updated_at = utc_now()
        path = self._path_for_namespace(normalized_namespace)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            try:
                tmp_path.replace(path)
            except PermissionError:
                path.write_text(tmp_path.read_text(encoding="utf-8"), encoding="utf-8")
                try:
                    tmp_path.unlink(missing_ok=True)
                except PermissionError:
                    pass
            self._cache[normalized_namespace] = (path.stat().st_mtime, state.model_copy(deep=True))

    def invalidate(self, namespace: str) -> None:
        self._cache.pop(_normalize_namespace(namespace), None)

    def list_namespaces(self) -> list[str]:
        if not self.base_path.exists():
            return []
        namespaces: list[str] = []
        for path in self.base_path.glob("*.json"):
            namespaces.append(path.stem.replace("__", "/"))
        return sorted(dict.fromkeys(namespaces))

    def _path_for_namespace(self, namespace: str) -> Path:
        safe = _normalize_namespace(namespace).replace("/", "__")
        return self.base_path / f"{safe}.json"


def _normalize_namespace(namespace: str) -> str:
    normalized = str(namespace or "global/default").strip().strip("/\\")
    return normalized or "global/default"
