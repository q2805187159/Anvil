from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path, PurePosixPath
import re
from urllib.parse import quote
from typing import Any

from pydantic import BaseModel, ConfigDict

from anvil.agents import ThreadDataState


THREAD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
USER_DATA_PREFIX = PurePosixPath("/mnt/user-data")
WORKER_DATA_PREFIX = PurePosixPath("/mnt/worker-data")
SKILLS_PREFIX = PurePosixPath("/mnt/skills")
HOST_WORKSPACE_PREFIX = USER_DATA_PREFIX / "workspace" / "_host"
VISIBLE_USER_DATA_DIRS = ("workspace", "uploads", "outputs")
HOST_ALIAS_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RUNTIME_VIRTUAL_PREFIXES = (
    USER_DATA_PREFIX.as_posix(),
    WORKER_DATA_PREFIX.as_posix(),
    SKILLS_PREFIX.as_posix(),
)


class ArtifactKind(str, Enum):
    UPLOADS = "uploads"
    OUTPUTS = "outputs"


class ArtifactDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    kind: ArtifactKind
    relative_path: str
    virtual_path: str
    artifact_url: str


class SandboxPathProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    policy_roots: list[str]
    logical_cwd: str


class RuntimePathRoot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    virtual_path: str
    kind: str
    description: str
    writable: bool = True
    display_root: str | None = None


@dataclass(frozen=True)
class PathBridge:
    alias: str
    display_root: str
    actual_root: str
    runtime_root: str
    display_windows: bool

    @classmethod
    def create(cls, *, alias: str, display_root: str, actual_root: str) -> "PathBridge":
        if not HOST_ALIAS_RE.fullmatch(alias):
            raise ValueError(f"invalid host path bridge alias: {alias!r}")
        normalized_display = display_root.rstrip("\\/") or display_root
        normalized_actual = str(Path(actual_root).resolve())
        return cls(
            alias=alias,
            display_root=normalized_display,
            actual_root=normalized_actual,
            runtime_root=(HOST_WORKSPACE_PREFIX / alias).as_posix(),
            display_windows=bool(re.match(r"^[A-Za-z]:(?:[\\/]|$)", normalized_display)),
        )

    def runtime_prefix(self) -> PurePosixPath:
        return PurePosixPath(self.runtime_root)


@dataclass(frozen=True)
class ThreadPathLayout:
    thread_root: Path
    workspace: Path
    uploads: Path
    outputs: Path
    worker_data: Path
    scratch: Path
    workspace_mode: str
    configured_workspace_root: str | None


class PathService:
    def __init__(
        self,
        base_root: Path,
        artifact_base_url: str = "/threads",
        path_bridges: list[PathBridge] | None = None,
        default_workspace_root: Path | None = None,
        default_workspace_mode: str = "thread",
    ):
        self.base_root = base_root
        self.artifact_base_url = artifact_base_url.rstrip("/")
        self.path_bridges = path_bridges or []
        self.default_workspace_root = default_workspace_root.resolve() if default_workspace_root is not None else None
        self.default_workspace_mode = self._normalize_workspace_mode(default_workspace_mode)

    def bootstrap_thread_paths(
        self,
        thread_id: str,
        *,
        workspace_root: str | Path | None = None,
        workspace_mode: str | None = None,
        clear_workspace_override: bool = False,
    ) -> ThreadDataState:
        self._validate_thread_id(thread_id)
        layout = self._bootstrap_layout(
            thread_id,
            workspace_root=workspace_root,
            workspace_mode=workspace_mode,
            clear_workspace_override=clear_workspace_override,
        )

        return ThreadDataState(
            workspace_path=str(layout.workspace),
            uploads_path=str(layout.uploads),
            outputs_path=str(layout.outputs),
            external_agent_workspace_root=str(layout.worker_data),
            workspace_mode=layout.workspace_mode,
            workspace_root=layout.configured_workspace_root,
        )

    def thread_storage_dir(self, thread_id: str) -> Path:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).thread_root

    def thread_workspace_dir(self, thread_id: str) -> Path:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).workspace

    def thread_uploads_dir(self, thread_id: str) -> Path:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).uploads

    def thread_outputs_dir(self, thread_id: str) -> Path:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).outputs

    def thread_workspace_mode(self, thread_id: str) -> str:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).workspace_mode

    def thread_workspace_root_setting(self, thread_id: str) -> str | None:
        self._validate_thread_id(thread_id)
        return self._thread_layout(thread_id).configured_workspace_root

    def thread_scratch_dir(self, thread_id: str) -> Path:
        self._validate_thread_id(thread_id)
        layout = self._thread_layout(thread_id)
        layout.scratch.mkdir(parents=True, exist_ok=True)
        return layout.scratch

    def resolve_virtual_path(self, thread_id: str, virtual_path: str) -> Path:
        self._validate_thread_id(thread_id)
        candidate = self._normalize_virtual_path(virtual_path)

        if candidate == USER_DATA_PREFIX:
            raise ValueError(
                "directory discovery only is supported at /mnt/user-data; "
                "use /mnt/user-data/workspace, /mnt/user-data/uploads, or /mnt/user-data/outputs"
            )

        bridge_resolution = self._resolve_bridge_virtual_path(candidate)
        if bridge_resolution is not None:
            host_root, remainder = bridge_resolution
            resolved = (host_root / remainder).resolve()
            allowed_root = host_root.resolve()
            self.ensure_within_allowed_root(thread_id, resolved, str(allowed_root))
            return resolved

        if self._is_under_prefix(candidate, USER_DATA_PREFIX / "workspace"):
            host_root = self.thread_workspace_dir(thread_id)
            remainder = candidate.relative_to(USER_DATA_PREFIX / "workspace")
        elif self._is_under_prefix(candidate, USER_DATA_PREFIX / "uploads"):
            host_root = self.thread_uploads_dir(thread_id)
            remainder = candidate.relative_to(USER_DATA_PREFIX / "uploads")
        elif self._is_under_prefix(candidate, USER_DATA_PREFIX / "outputs"):
            host_root = self.thread_outputs_dir(thread_id)
            remainder = candidate.relative_to(USER_DATA_PREFIX / "outputs")
        elif self._is_under_prefix(candidate, WORKER_DATA_PREFIX):
            relative = candidate.relative_to(WORKER_DATA_PREFIX)
            if len(relative.parts) < 2 or relative.parts[1] != "workspace":
                raise ValueError(f"invalid worker-data virtual path: {virtual_path}")
            host_root = self._thread_layout(thread_id).worker_data / relative.parts[0] / "workspace"
            remainder = Path(*relative.parts[2:])
        elif self._is_under_prefix(candidate, SKILLS_PREFIX):
            raise ValueError("skills prefix is reserved and not resolved by thread-local path service")
        else:
            raise ValueError(f"unsupported virtual path prefix: {virtual_path}")

        resolved = (host_root / remainder).resolve()
        allowed_root = host_root.resolve()
        self.ensure_within_allowed_root(thread_id, resolved, str(allowed_root))
        return resolved

    def list_virtual_dir(self, thread_id: str, virtual_path: str) -> list[str]:
        self._validate_thread_id(thread_id)
        candidate = self._normalize_virtual_path(virtual_path)

        if candidate == USER_DATA_PREFIX:
            return sorted(VISIBLE_USER_DATA_DIRS)

        if candidate == HOST_WORKSPACE_PREFIX:
            return sorted(bridge.alias for bridge in self.path_bridges)

        if candidate == USER_DATA_PREFIX / "workspace":
            host_path = self.resolve_virtual_path(thread_id, virtual_path)
            if not host_path.is_dir():
                raise ValueError(f"path is not a directory: {virtual_path}")
            names = [child.name for child in host_path.iterdir()]
            if self.path_bridges:
                names.append("_host")
            return sorted(set(names))

        host_path = self.resolve_virtual_path(thread_id, virtual_path)
        if not host_path.is_dir():
            raise ValueError(f"path is not a directory: {virtual_path}")
        return sorted(child.name for child in host_path.iterdir())

    def to_virtual_path(self, thread_id: str, host_path: str | Path) -> str:
        self._validate_thread_id(thread_id)
        resolved = Path(host_path).resolve()
        layout = self._thread_layout(thread_id)

        root_mappings = {
            layout.workspace: USER_DATA_PREFIX / "workspace",
            layout.uploads: USER_DATA_PREFIX / "uploads",
            layout.outputs: USER_DATA_PREFIX / "outputs",
            layout.worker_data: WORKER_DATA_PREFIX,
            layout.scratch: USER_DATA_PREFIX / "workspace" / ".anvil-scratch",
        }

        for host_root, virtual_root in root_mappings.items():
            try:
                relative = resolved.relative_to(host_root)
            except ValueError:
                continue

            return str((virtual_root / relative).as_posix())

        for bridge in self.path_bridges:
            actual_root = Path(bridge.actual_root).resolve()
            try:
                relative = resolved.relative_to(actual_root)
            except ValueError:
                continue
            return str((bridge.runtime_prefix() / relative).as_posix())

        raise ValueError(f"path is outside the thread roots: {resolved}")

    def to_artifact_descriptor(
        self,
        thread_id: str,
        kind: ArtifactKind | str,
        relative_path: str,
    ) -> ArtifactDescriptor:
        self._validate_thread_id(thread_id)
        artifact_kind = ArtifactKind(kind)
        normalized_relative = self._normalize_relative_path(relative_path)
        virtual_root = USER_DATA_PREFIX / artifact_kind.value
        virtual_path = str((virtual_root / normalized_relative).as_posix())
        artifact_url = (
            f"{self.artifact_base_url}/{thread_id}/artifacts/{artifact_kind.value}/"
            f"{quote(normalized_relative.as_posix(), safe='/')}"
        )

        return ArtifactDescriptor(
            thread_id=thread_id,
            kind=artifact_kind,
            relative_path=normalized_relative.as_posix(),
            virtual_path=virtual_path,
            artifact_url=artifact_url,
        )

    def ensure_within_allowed_root(self, thread_id: str, host_path: str | Path, allowed_root: str | Path) -> None:
        self._validate_thread_id(thread_id)
        resolved_host = Path(host_path).resolve()
        resolved_root = Path(allowed_root).resolve()
        try:
            resolved_host.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"path escapes allowed root: {resolved_host}") from exc

    def ensure_within_any_allowed_root(
        self,
        thread_id: str,
        host_path: str | Path,
        allowed_roots: list[str] | tuple[str | Path, ...],
    ) -> None:
        self._validate_thread_id(thread_id)
        last_error: ValueError | None = None
        for root in allowed_roots:
            try:
                self.ensure_within_allowed_root(thread_id, host_path, root)
                return
            except ValueError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ValueError(f"no allowed roots configured for thread: {thread_id}")

    def to_sandbox_projection(
        self,
        thread_id: str,
        logical_cwd: str = "/mnt/user-data/workspace",
        writable_kinds: tuple[ArtifactKind | str, ...] = ("workspace",),
    ) -> SandboxPathProjection:
        self._validate_thread_id(thread_id)
        logical_cwd_path = self._normalize_virtual_path(logical_cwd)
        if not self._is_under_prefix(logical_cwd_path, USER_DATA_PREFIX):
            raise ValueError(f"logical cwd must stay under {USER_DATA_PREFIX.as_posix()}: {logical_cwd}")

        policy_roots: list[str] = []
        layout = self._thread_layout(thread_id)
        for kind in writable_kinds:
            kind_value = kind.value if isinstance(kind, ArtifactKind) else kind
            if kind_value == "workspace":
                policy_roots.append(str(layout.workspace))
            elif kind_value == ArtifactKind.UPLOADS.value:
                policy_roots.append(str(layout.uploads))
            elif kind_value == ArtifactKind.OUTPUTS.value:
                policy_roots.append(str(layout.outputs))
            else:
                raise ValueError(f"unsupported writable kind: {kind_value}")

        for bridge in self.path_bridges:
            policy_roots.append(str(Path(bridge.actual_root).resolve()))

        return SandboxPathProjection(
            thread_id=thread_id,
            policy_roots=policy_roots,
            logical_cwd=logical_cwd_path.as_posix(),
        )

    def translate_user_text_to_runtime(self, text: str | None, thread_id: str | None = None) -> str | None:
        if text is None:
            return None
        translated = text
        if thread_id is not None:
            translated = self._translate_thread_actual_text_to_runtime(translated, thread_id)
        for bridge in sorted(self.path_bridges, key=lambda item: len(item.display_root), reverse=True):
            translated = self._translate_display_root_to_runtime(translated, bridge)
        return translated

    def translate_runtime_text_to_display(self, text: str | None, thread_id: str | None = None) -> str | None:
        if text is None:
            return None
        translated = text
        for bridge in sorted(self.path_bridges, key=lambda item: len(item.runtime_root), reverse=True):
            translated = self._translate_runtime_root_to_display(translated, bridge)
        if thread_id is not None:
            translated = self._translate_thread_virtual_text_to_actual(translated, thread_id)
        return translated

    def translate_runtime_text_to_host(self, text: str | None, thread_id: str | None = None) -> str | None:
        if text is None:
            return None
        translated = text
        for bridge in sorted(self.path_bridges, key=lambda item: len(item.runtime_root), reverse=True):
            translated = self._translate_runtime_root_to_actual(translated, bridge)
        if thread_id is not None:
            translated = self._translate_thread_virtual_text_to_actual(translated, thread_id)
        return translated

    def translate_runtime_data_to_host(self, value: Any, thread_id: str | None = None) -> Any:
        if isinstance(value, str):
            return self.translate_runtime_text_to_host(value, thread_id=thread_id)
        if isinstance(value, list):
            return [self.translate_runtime_data_to_host(item, thread_id=thread_id) for item in value]
        if isinstance(value, tuple):
            return tuple(self.translate_runtime_data_to_host(item, thread_id=thread_id) for item in value)
        if isinstance(value, dict):
            return {
                key: self.translate_runtime_data_to_host(item, thread_id=thread_id)
                for key, item in value.items()
            }
        return value

    def virtual_path_map(self, thread_id: str) -> dict[str, str]:
        """Return exact virtual-root mappings for child process shims.

        Shell commands are translated before execution, but generated scripts can
        still contain hard-coded `/mnt/user-data/...` paths. The runtime injects
        this mapping into Python child processes so file APIs see the same
        thread-local filesystem contract as tools.
        """

        self._validate_thread_id(thread_id)
        layout = self._thread_layout(thread_id)
        mappings = {
            "/mnt/user-data/workspace": str(layout.workspace),
            "/mnt/user-data/uploads": str(layout.uploads),
            "/mnt/user-data/outputs": str(layout.outputs),
            "/mnt/worker-data": str(layout.worker_data),
        }
        for bridge in self.path_bridges:
            mappings[bridge.runtime_root] = str(Path(bridge.actual_root).resolve())
        return mappings

    def visible_runtime_roots(self, thread_id: str) -> tuple[RuntimePathRoot, ...]:
        self._validate_thread_id(thread_id)
        mode = self.thread_workspace_mode(thread_id)
        roots = [
            RuntimePathRoot(
                virtual_path="/mnt/user-data/workspace",
                kind="workspace",
                description=f"active thread workspace ({mode})",
                display_root=self.thread_workspace_root_setting(thread_id),
            ),
            RuntimePathRoot(
                virtual_path="/mnt/user-data/uploads",
                kind="uploads",
                description="files uploaded into this thread",
            ),
            RuntimePathRoot(
                virtual_path="/mnt/user-data/outputs",
                kind="outputs",
                description="files produced for download or handoff",
            ),
        ]
        for bridge in self.path_bridges:
            roots.append(
                RuntimePathRoot(
                    virtual_path=bridge.runtime_root,
                    kind="host_bridge",
                    description=f"configured host path bridge '{bridge.alias}'",
                    display_root=bridge.display_root,
                )
            )
        return tuple(roots)

    def translate_runtime_text_to_virtual(self, text: str | None, thread_id: str | None = None) -> str | None:
        return self.translate_user_text_to_runtime(text, thread_id=thread_id)

    def translate_runtime_data_to_virtual(self, value: Any, thread_id: str | None = None) -> Any:
        return self.translate_user_data_to_runtime(value, thread_id=thread_id)

    def list_artifact_relative_paths(self, thread_id: str, kind: ArtifactKind | str) -> list[str]:
        kind_value = kind.value if isinstance(kind, ArtifactKind) else str(kind)
        if kind_value == ArtifactKind.UPLOADS.value:
            root = self.thread_uploads_dir(thread_id)
        elif kind_value == ArtifactKind.OUTPUTS.value:
            root = self.thread_outputs_dir(thread_id)
        else:
            raise ValueError(f"unsupported artifact kind: {kind_value}")
        if not root.exists():
            return []
        return sorted(
            file_path.relative_to(root).as_posix()
            for file_path in root.rglob("*")
            if file_path.is_file()
        )

    def translate_runtime_data_to_display(self, value: Any, thread_id: str | None = None) -> Any:
        if isinstance(value, str):
            return self.translate_runtime_text_to_display(value, thread_id=thread_id)
        if isinstance(value, list):
            return [self.translate_runtime_data_to_display(item, thread_id=thread_id) for item in value]
        if isinstance(value, tuple):
            return tuple(self.translate_runtime_data_to_display(item, thread_id=thread_id) for item in value)
        if isinstance(value, dict):
            return {
                key: self.translate_runtime_data_to_display(item, thread_id=thread_id)
                for key, item in value.items()
            }
        return value

    def translate_user_data_to_runtime(self, value: Any, thread_id: str | None = None) -> Any:
        if isinstance(value, str):
            return self.translate_user_text_to_runtime(value, thread_id=thread_id)
        if isinstance(value, list):
            return [self.translate_user_data_to_runtime(item, thread_id=thread_id) for item in value]
        if isinstance(value, tuple):
            return tuple(self.translate_user_data_to_runtime(item, thread_id=thread_id) for item in value)
        if isinstance(value, dict):
            return {
                key: self.translate_user_data_to_runtime(item, thread_id=thread_id)
                for key, item in value.items()
            }
        return value

    def _thread_root(self, thread_id: str) -> Path:
        return self.base_root / thread_id

    def _thread_layout_file(self, thread_id: str) -> Path:
        return self._thread_root(thread_id) / ".anvil-thread-paths.json"

    def _validate_thread_id(self, thread_id: str) -> None:
        if not THREAD_ID_RE.fullmatch(thread_id):
            raise ValueError(f"invalid thread id: {thread_id!r}")

    def _resolve_bridge_virtual_path(self, candidate: PurePosixPath) -> tuple[Path, Path] | None:
        for bridge in self.path_bridges:
            runtime_root = bridge.runtime_prefix()
            if self._is_under_prefix(candidate, runtime_root):
                remainder = candidate.relative_to(runtime_root)
                return Path(bridge.actual_root), Path(*remainder.parts)
        return None

    def _thread_layout(self, thread_id: str) -> ThreadPathLayout:
        self._validate_thread_id(thread_id)
        layout_file = self._thread_layout_file(thread_id)
        if layout_file.exists():
            payload = json.loads(layout_file.read_text(encoding="utf-8"))
            workspace_mode = self._normalize_workspace_mode(str(payload.get("workspace_mode") or "thread"))
            configured_workspace_root = payload.get("workspace_root")
            return self._build_layout(
                thread_id,
                workspace_path=Path(str(payload["workspace_path"])).resolve(),
                workspace_mode=workspace_mode,
                configured_workspace_root=str(configured_workspace_root) if isinstance(configured_workspace_root, str) and configured_workspace_root.strip() else None,
            )
        return self._build_layout(
            thread_id,
            workspace_path=self._default_workspace_path(thread_id),
            workspace_mode=self._default_workspace_mode_for_thread(),
            configured_workspace_root=str(self.default_workspace_root) if self._default_workspace_mode_for_thread() == "external" and self.default_workspace_root is not None else None,
        )

    def _bootstrap_layout(
        self,
        thread_id: str,
        *,
        workspace_root: str | Path | None,
        workspace_mode: str | None,
        clear_workspace_override: bool,
    ) -> ThreadPathLayout:
        thread_root = self._thread_root(thread_id).resolve()
        thread_root.mkdir(parents=True, exist_ok=True)

        if clear_workspace_override:
            effective_workspace_mode = self._default_workspace_mode_for_thread()
            effective_workspace_path = self._default_workspace_path(thread_id)
            configured_workspace_root = (
                str(self.default_workspace_root)
                if effective_workspace_mode == "external" and self.default_workspace_root is not None
                else None
            )
        elif workspace_root is not None:
            effective_workspace_mode = "external"
            effective_workspace_path = Path(workspace_root).expanduser().resolve()
            configured_workspace_root = str(effective_workspace_path)
        else:
            effective_workspace_mode = self._normalize_workspace_mode(workspace_mode or self._default_workspace_mode_for_thread())
            if effective_workspace_mode == "external" and self.default_workspace_root is not None:
                effective_workspace_path = self.default_workspace_root
                configured_workspace_root = str(self.default_workspace_root)
            else:
                effective_workspace_mode = "thread"
                effective_workspace_path = (thread_root / "workspace").resolve()
                configured_workspace_root = None

        layout = self._build_layout(
            thread_id,
            workspace_path=effective_workspace_path,
            workspace_mode=effective_workspace_mode,
            configured_workspace_root=configured_workspace_root,
        )
        for path in (layout.thread_root, layout.workspace, layout.uploads, layout.outputs, layout.worker_data, layout.scratch):
            path.mkdir(parents=True, exist_ok=True)
        self._thread_layout_file(thread_id).write_text(
            json.dumps(
                {
                    "workspace_path": str(layout.workspace),
                    "workspace_mode": layout.workspace_mode,
                    "workspace_root": layout.configured_workspace_root,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return layout

    def _build_layout(
        self,
        thread_id: str,
        *,
        workspace_path: Path,
        workspace_mode: str,
        configured_workspace_root: str | None,
    ) -> ThreadPathLayout:
        thread_root = self._thread_root(thread_id).resolve()
        return ThreadPathLayout(
            thread_root=thread_root,
            workspace=workspace_path.resolve(),
            uploads=(thread_root / "uploads").resolve(),
            outputs=(thread_root / "outputs").resolve(),
            worker_data=(thread_root / "worker_data").resolve(),
            scratch=(thread_root / ".anvil-scratch").resolve(),
            workspace_mode=self._normalize_workspace_mode(workspace_mode),
            configured_workspace_root=configured_workspace_root,
        )

    def _default_workspace_mode_for_thread(self) -> str:
        if self.default_workspace_root is not None and self.default_workspace_mode == "external":
            return "external"
        return "thread"

    def _default_workspace_path(self, thread_id: str) -> Path:
        if self.default_workspace_root is not None and self.default_workspace_mode == "external":
            return self.default_workspace_root
        return (self._thread_root(thread_id) / "workspace").resolve()

    def _normalize_workspace_mode(self, value: str | None) -> str:
        if value is None:
            return "thread"
        normalized = value.strip().lower()
        if normalized in {"external", "project"}:
            return "external"
        return "thread"

    def _normalize_relative_path(self, value: str) -> Path:
        candidate = Path(value)
        if candidate.is_absolute():
            raise ValueError("artifact relative path must not be absolute")
        if ".." in candidate.parts:
            raise ValueError("artifact relative path must not contain traversal")
        return Path(*candidate.parts)

    def _normalize_virtual_path(self, virtual_path: str) -> PurePosixPath:
        if not virtual_path:
            raise ValueError("virtual path must not be empty")
        if "\\" in virtual_path:
            raise ValueError(f"unsupported virtual path prefix: {virtual_path}")

        candidate = PurePosixPath(virtual_path)
        if not candidate.is_absolute():
            raise ValueError(f"unsupported virtual path prefix: {virtual_path}")
        if ".." in candidate.parts:
            raise ValueError(f"path escapes allowed root: {virtual_path}")

        normalized_parts = [part for part in candidate.parts if part not in {"."}]
        if not normalized_parts:
            return PurePosixPath("/")
        return PurePosixPath(*normalized_parts)

    def _is_under_prefix(self, candidate: PurePosixPath, prefix: PurePosixPath) -> bool:
        try:
            candidate.relative_to(prefix)
            return True
        except ValueError:
            return False

    def _translate_display_root_to_runtime(self, text: str, bridge: PathBridge) -> str:
        pattern = self._display_pattern(bridge)

        def replace(match: re.Match[str]) -> str:
            full_path = match.group(0)
            if _is_runtime_virtual_path_text(full_path):
                return full_path
            relative = self._display_relative(full_path, bridge)
            runtime_path = bridge.runtime_root
            if relative:
                runtime_path = f"{runtime_path}/{relative}"
            return runtime_path

        return pattern.sub(replace, text)

    def _translate_runtime_root_to_display(self, text: str, bridge: PathBridge) -> str:
        pattern = re.compile(re.escape(bridge.runtime_root) + r"(?P<rest>(?:/[^\s\"'<>|]+)*)")

        def replace(match: re.Match[str]) -> str:
            rest = match.group("rest") or ""
            if not rest:
                return bridge.display_root
            display_sep = "\\" if bridge.display_windows else "/"
            return bridge.display_root + rest.replace("/", display_sep)

        return pattern.sub(replace, text)

    def _translate_runtime_root_to_actual(self, text: str, bridge: PathBridge) -> str:
        pattern = re.compile(re.escape(bridge.runtime_root) + r"(?P<rest>(?:/[^\s\"'<>|]+)*)")

        def replace(match: re.Match[str]) -> str:
            rest = match.group("rest") or ""
            return bridge.actual_root + rest.replace("/", os_sep(bridge.actual_root))

        return pattern.sub(replace, text)

    def _translate_thread_actual_text_to_runtime(self, text: str, thread_id: str) -> str:
        layout = self._thread_layout(thread_id)
        mappings = [
            (str(layout.workspace), "/mnt/user-data/workspace"),
            (str(layout.uploads), "/mnt/user-data/uploads"),
            (str(layout.outputs), "/mnt/user-data/outputs"),
        ]
        translated = text
        for display_root, runtime_root in mappings:
            pattern = self._actual_root_pattern(display_root)
            translated = pattern.sub(
                lambda match: runtime_root + (match.group("rest") or "").replace("\\", "/"),
                translated,
            )
        return translated

    def _actual_root_pattern(self, root: str) -> re.Pattern[str]:
        escaped = re.escape(root)
        if "\\" in root:
            escaped = escaped.replace(r"\\", r"[\\/]")
        return re.compile(escaped + r"(?P<rest>(?:[\\/][^\s\"'<>|]+)*)", re.IGNORECASE if re.match(r"^[A-Za-z]:", root) else 0)

    def _translate_thread_virtual_text_to_actual(self, text: str, thread_id: str) -> str:
        layout = self._thread_layout(thread_id)
        mappings = [
            ("/mnt/user-data/workspace", str(layout.workspace)),
            ("/mnt/user-data/uploads", str(layout.uploads)),
            ("/mnt/user-data/outputs", str(layout.outputs)),
        ]
        translated = text
        for runtime_root, actual_root in mappings:
            pattern = re.compile(re.escape(runtime_root) + r"(?P<rest>(?:/[^\s\"'<>|]+)*)")
            translated = pattern.sub(
                lambda match: actual_root + (match.group("rest") or "").replace("/", os_sep(actual_root)),
                translated,
            )
        return translated

    def _display_pattern(self, bridge: PathBridge) -> re.Pattern[str]:
        escaped = re.escape(bridge.display_root)
        if bridge.display_windows:
            escaped = escaped.replace(r"\\", r"[\\/]")
            prefix = r"(?<![A-Za-z0-9_])" if re.fullmatch(r"[A-Za-z]:", bridge.display_root) else ""
            return re.compile(prefix + escaped + r"(?P<rest>(?:[\\/][^\s\"'<>|]+)*)", re.IGNORECASE)
        return re.compile(escaped + r"(?P<rest>(?:/[^\s\"'<>|]+)*)")

    def _display_relative(self, full_path: str, bridge: PathBridge) -> str:
        if bridge.display_windows:
            normalized = full_path.replace("/", "\\")
            root = bridge.display_root.replace("/", "\\")
            relative = normalized[len(root):]
            return relative.lstrip("\\/").replace("\\", "/")
        relative = full_path[len(bridge.display_root):]
        return relative.lstrip("/")


def os_sep(path_text: str) -> str:
    return "\\" if re.match(r"^[A-Za-z]:\\", path_text) else "/"


def _is_runtime_virtual_path_text(value: str) -> bool:
    normalized = value.replace("\\", "/").rstrip("/")
    return any(normalized == prefix or normalized.startswith(f"{prefix}/") for prefix in RUNTIME_VIRTUAL_PREFIXES)
