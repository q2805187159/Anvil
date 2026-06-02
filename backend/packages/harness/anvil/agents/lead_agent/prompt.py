from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from anvil.agents.features import RuntimeFeatureSet
from anvil.runtime.tool_registry.contracts import CapabilityBundle


class PromptSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    content: str

    def render(self) -> str:
        return f"<{self.name}>\n{self.content}\n</{self.name}>"


class PromptSnapshotKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config_fingerprint: str
    capability_bundle_fingerprint: str
    enabled_skill_summary_fingerprint: str
    policy_version: str
    memory_namespace: str | None = None
    memory_snapshot_fingerprint: str | None = None
    project_context_fingerprint: str | None = None
    runtime_path_fingerprint: str | None = None
    runtime_mode: str = "lead_agent"

    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


class PromptSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    snapshot_key: PromptSnapshotKey
    stable_sections: list[PromptSection] = Field(default_factory=list)
    version: str = "phase4-v1"

    def render(self) -> str:
        return "\n\n".join(section.render() for section in self.stable_sections)


class PromptInjectionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_context: str | None = None
    upload_context: str | None = None
    approval_context: str | None = None
    plan_context: str | None = None
    memory_context: str | None = None
    promoted_capabilities: tuple[str, ...] = ()

    def sections(self) -> list[PromptSection]:
        sections: list[PromptSection] = []
        if self.request_context:
            sections.append(PromptSection(name="request_context", content=self.request_context))
        if self.upload_context:
            sections.append(PromptSection(name="upload_context", content=self.upload_context))
        if self.approval_context:
            sections.append(PromptSection(name="approval_context", content=self.approval_context))
        if self.plan_context:
            sections.append(PromptSection(name="plan_context", content=self.plan_context))
        if self.memory_context:
            sections.append(PromptSection(name="memory_context", content=self.memory_context))
        if self.promoted_capabilities:
            promoted = "\n".join(f"- {name}" for name in self.promoted_capabilities)
            sections.append(PromptSection(name="promoted_capabilities", content=promoted))
        return sections

    def render(self) -> str:
        sections = self.sections()
        return "\n\n".join(section.render() for section in sections)


class RuntimePathContextSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rendered: str
    fingerprint: str
    root_count: int = 0
    host_bridge_count: int = 0
    cache_status: str | None = None


@dataclass(frozen=True)
class PromptSnapshotCacheStats:
    max_entries: int
    size: int
    hits: int
    misses: int
    writes: int
    evictions: int
    bypasses: int


class PromptSnapshotCache:
    def __init__(self, *, max_entries: int = 256) -> None:
        self.max_entries = max(max_entries, 1)
        self._items: OrderedDict[str, PromptSnapshot] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0
        self.bypasses = 0

    def get(self, key: str) -> PromptSnapshot | None:
        snapshot = self._items.get(key)
        if snapshot is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return snapshot

    def put(self, key: str, snapshot: PromptSnapshot) -> None:
        self.writes += 1
        self._items[key] = snapshot
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
            self.evictions += 1

    def bypass(self) -> None:
        self.bypasses += 1

    def reset(self, *, max_entries: int | None = None) -> None:
        if max_entries is not None:
            self.max_entries = max(max_entries, 1)
        self._items.clear()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0
        self.bypasses = 0

    def stats(self) -> PromptSnapshotCacheStats:
        return PromptSnapshotCacheStats(
            max_entries=self.max_entries,
            size=len(self._items),
            hits=self.hits,
            misses=self.misses,
            writes=self.writes,
            evictions=self.evictions,
            bypasses=self.bypasses,
        )


@dataclass(frozen=True)
class RuntimePathContextCacheStats:
    max_entries: int
    size: int
    hits: int
    misses: int
    writes: int
    evictions: int


class RuntimePathContextCache:
    def __init__(self, *, max_entries: int = 128) -> None:
        self.max_entries = max(max_entries, 1)
        self._items: OrderedDict[str, RuntimePathContextSnapshot] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0

    def get(self, key: str) -> RuntimePathContextSnapshot | None:
        snapshot = self._items.get(key)
        if snapshot is None:
            self.misses += 1
            return None
        self.hits += 1
        self._items.move_to_end(key)
        return snapshot.model_copy(update={"cache_status": "hit"}, deep=True)

    def put(self, key: str, snapshot: RuntimePathContextSnapshot) -> RuntimePathContextSnapshot:
        self.writes += 1
        stored = snapshot.model_copy(update={"cache_status": "miss"}, deep=True)
        self._items[key] = stored
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)
            self.evictions += 1
        return stored.model_copy(deep=True)

    def reset(self, *, max_entries: int | None = None) -> None:
        if max_entries is not None:
            self.max_entries = max(max_entries, 1)
        self._items.clear()
        self.hits = 0
        self.misses = 0
        self.writes = 0
        self.evictions = 0

    def stats(self) -> RuntimePathContextCacheStats:
        return RuntimePathContextCacheStats(
            max_entries=self.max_entries,
            size=len(self._items),
            hits=self.hits,
            misses=self.misses,
            writes=self.writes,
            evictions=self.evictions,
        )


_PROMPT_CACHE = PromptSnapshotCache()
_RUNTIME_PATH_CONTEXT_CACHE = RuntimePathContextCache()


def prompt_snapshot_cache_stats() -> PromptSnapshotCacheStats:
    return _PROMPT_CACHE.stats()


def reset_prompt_snapshot_cache(*, max_entries: int | None = None) -> None:
    _PROMPT_CACHE.reset(max_entries=max_entries)


def runtime_path_context_cache_stats() -> RuntimePathContextCacheStats:
    return _RUNTIME_PATH_CONTEXT_CACHE.stats()


def reset_runtime_path_context_cache(*, max_entries: int | None = None) -> None:
    _RUNTIME_PATH_CONTEXT_CACHE.reset(max_entries=max_entries)


def build_prompt_snapshot(
    *,
    config_fingerprint: str,
    capability_bundle: CapabilityBundle,
    feature_set: RuntimeFeatureSet,
    policy_version: str = "v1",
    memory_namespace: str | None = None,
    memory_snapshot: str | None = None,
    memory_snapshot_fingerprint: str | None = None,
    project_context: str | None = None,
    project_context_fingerprint: str | None = None,
    runtime_path_context: str | None = None,
    runtime_path_fingerprint: str | None = None,
    runtime_mode: str = "lead_agent",
    delegation_max_concurrency: int | None = None,
    delegation_max_depth: int | None = None,
) -> PromptSnapshot:
    memory_snapshot_fingerprint = memory_snapshot_fingerprint or _fingerprint_text(memory_snapshot)
    project_context_fingerprint = project_context_fingerprint or _fingerprint_text(project_context)
    runtime_path_fingerprint = runtime_path_fingerprint or _fingerprint_text(runtime_path_context)
    skill_summary_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "enabled_skill_ids": capability_bundle.enabled_skill_ids,
                "prompt_safe_summaries": capability_bundle.prompt_safe_summaries,
                "stable_prompt_cache": feature_set.stable_prompt_cache,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    key = PromptSnapshotKey(
        config_fingerprint=config_fingerprint,
        capability_bundle_fingerprint=capability_bundle.fingerprint,
        enabled_skill_summary_fingerprint=skill_summary_fingerprint,
        policy_version=policy_version,
        memory_namespace=memory_namespace,
        memory_snapshot_fingerprint=memory_snapshot_fingerprint,
        project_context_fingerprint=project_context_fingerprint,
        runtime_path_fingerprint=runtime_path_fingerprint,
        runtime_mode=runtime_mode,
    )
    digest = key.digest()
    if feature_set.stable_prompt_cache:
        cached = _PROMPT_CACHE.get(digest)
        if cached is not None:
            return cached
    else:
        _PROMPT_CACHE.bypass()

    snapshot = PromptSnapshot(
        snapshot_id=digest,
        snapshot_key=key,
        stable_sections=_build_stable_sections(
            capability_bundle,
            memory_snapshot=memory_snapshot,
            project_context=project_context,
            runtime_path_context=runtime_path_context,
            delegation_max_concurrency=delegation_max_concurrency,
            delegation_max_depth=delegation_max_depth,
        ),
    )
    if feature_set.stable_prompt_cache:
        _PROMPT_CACHE.put(digest, snapshot)
    return snapshot


def _fingerprint_text(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_turn_injection_view(
    *,
    request_context: str | None = None,
    upload_context: str | None = None,
    approval_context: str | None = None,
    plan_context: str | None = None,
    memory_context: str | None = None,
    promoted_capabilities: tuple[str, ...] = (),
) -> PromptInjectionView:
    return PromptInjectionView(
        request_context=request_context,
        upload_context=upload_context,
        approval_context=approval_context,
        plan_context=plan_context,
        memory_context=memory_context,
        promoted_capabilities=promoted_capabilities,
    )


def compose_system_prompt(snapshot: PromptSnapshot, injections: PromptInjectionView | None = None) -> str:
    stable = snapshot.render()
    if injections is None:
        return stable
    volatile = injections.render()
    return stable if not volatile else f"{stable}\n\n{volatile}"


def build_runtime_path_context(*, path_service, thread_id: str) -> RuntimePathContextSnapshot:
    roots = [
        item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item)
        for item in path_service.visible_runtime_roots(thread_id)
    ]
    cache_key = _runtime_path_context_cache_key(path_service=path_service, thread_id=thread_id, roots=roots)
    cached = _RUNTIME_PATH_CONTEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    lines = []
    for root in roots:
        display = root.get("display_root")
        display_suffix = f" -> {display}" if display else ""
        lines.append(
            f"- {root['virtual_path']}: {root['description']}{display_suffix}; writable={root.get('writable', True)}"
        )
    if not any(item.get("kind") == "host_bridge" for item in roots):
        lines.append(
            "- Host paths outside the roots above are not visible. Local deployments can expose host roots automatically or through workspace.path_bridges; remote/provider sandboxes only expose roots that are listed here."
        )
    payload = json.dumps(roots, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return _RUNTIME_PATH_CONTEXT_CACHE.put(
        cache_key,
        RuntimePathContextSnapshot(
            rendered="\n".join(lines),
            fingerprint=fingerprint,
            root_count=len(roots),
            host_bridge_count=sum(1 for item in roots if item.get("kind") == "host_bridge"),
        ),
    )


def _runtime_path_context_cache_key(*, path_service, thread_id: str, roots: list[dict[str, object]]) -> str:
    payload = {
        "thread_id": thread_id,
        "path_service": {
            "base_root": _path_value(getattr(path_service, "base_root", None)),
            "artifact_base_url": str(getattr(path_service, "artifact_base_url", "")),
            "default_workspace_root": _path_value(getattr(path_service, "default_workspace_root", None)),
            "default_workspace_mode": str(getattr(path_service, "default_workspace_mode", "")),
        },
        "workspace_mode": _safe_path_service_call(path_service, "thread_workspace_mode", thread_id),
        "workspace_root": _safe_path_service_call(path_service, "thread_workspace_root_setting", thread_id),
        "roots": roots,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _path_value(value) -> str | None:
    if value is None:
        return None
    try:
        return value.resolve().as_posix()
    except Exception:
        return str(value)


def _safe_path_service_call(path_service, method_name: str, thread_id: str) -> str | None:
    method = getattr(path_service, method_name, None)
    if not callable(method):
        return None
    try:
        value = method(thread_id)
    except Exception:
        return None
    return None if value is None else str(value)


def _build_stable_sections(
    capability_bundle: CapabilityBundle,
    *,
    memory_snapshot: str | None = None,
    project_context: str | None = None,
    runtime_path_context: str | None = None,
    delegation_max_concurrency: int | None = None,
    delegation_max_depth: int | None = None,
) -> list[PromptSection]:
    capability_summary = (
        "\n".join(capability_bundle.prompt_safe_summaries)
        if capability_bundle.prompt_safe_summaries
        else "No visible capabilities are currently exposed."
    )
    deferred_summary = (
        "\n".join(f"- {tool.name}" for tool in capability_bundle.deferred_tools)
        if capability_bundle.deferred_tools
        else "No deferred capabilities are registered."
    )

    sections = [
        PromptSection(
            name="role_and_intent",
            content=(
                "You are the Anvil lead agent runtime. "
                "You orchestrate tools and horizontal runtime capabilities without leaking app-layer concerns into execution."
            ),
        ),
        PromptSection(
            name="operating_principles",
            content=(
                "Prefer direct, correct execution. Preserve runtime truth from the registry and middleware stack. "
                "Do not invent capabilities that are not currently exposed. "
                "Prefer the narrowest stable tool that solves the task before escalating to shell execution."
            ),
        ),
        PromptSection(
            name="workflow_rules",
            content=(
                "**Efficiency First**: Minimize message rounds by planning ahead and batching independent operations. "
                "For simple tasks (1-3 steps), execute all steps in a single turn when possible. "
                "For complex tasks, identify all required steps upfront, then execute independent steps together. "
                "Example: 'create file.txt and read it' → call write_file AND read_file in one turn, not sequentially. "
                "Example: 'create 3 files' → call write_file three times in one turn, not across three turns. "
                "\n\n"
                "Follow clarify -> plan -> act. Clarify before irreversible work, not after it. "
                "If approval or permissions are required, stop and surface that requirement instead of guessing. "
                "Typed approval outcomes are skip, needs_user_approval, or forbidden. "
                "For filesystem work, discover directory roots with list_dir, use list_dir(structured=true) or file_info when metadata matters, search names/content with search_files or the thin glob_files/grep_files aliases, inspect large files with read_file line windows and numbered=true when patching, "
                "prefer patch_file for focused edits to existing files, use dry_run=true before high-risk patches, and use write_file only when creating a new file or intentionally replacing the full contents. "
                "For coding tasks specifically, use the narrowest coding tool that answers the next question: "
                "code_symbols for one-file outlines, code_symbol_search for name lookup, code_definition for implementations, code_references for bounded usages, "
                "code_file_summary for single-file metadata, code_impact before editing shared/public code, and code_map only when a compact project index is needed. "
                "When the workspace contains multiple nested projects, set project_path on code_map/code_symbols/code_focus/code_file_summary/code_impact/code_health and related coding tools to the exact virtual project root before analyzing. "
                "Use code_focus for one-file dependency context, code_security_scan for security findings, code_pattern_scan for patterns, code_doc_graph for docs links, and code_health for project health; do not request all coding-analysis surfaces when one will do. "
                "For capability discovery use tool_catalog/tool_view, toolset_catalog/toolset_view, and capability_search before guessing names or schemas. "
                "Large external tool catalogs may be task-filtered; if a likely tool is deferred or missing, use capability_search or tool_catalog with a specific query before assuming it is unavailable. "
                "For governed surfaces use only Anvil skill tools: skills_list, skill_view, skill_content, skill_files, and skill_read_file. "
                "Do not call legacy external skill-download tools that mention .claude/skills; Anvil skills are already governed by the registry and must be accessed through skill_id plus relative_path. "
                "For document extraction and Word export, prefer extract_document and export_document before falling back to run_command. "
                "If a request requires a user decision, call ask_clarification with structured options so clients can render a choice UI and resume with the user's selection. "
                "When multiple related decisions are needed, use ask_clarification fields to bundle them into one typed form instead of emitting a markdown checklist or separate pause requests. "
                "PPT generation tools are not exposed in this runtime surface; for deck requests, clarify style, audience, page count, and content requirements through structured interaction fields, then produce governed source artifacts or explicitly approved external workflows instead. "
                "Do not write draft markdown, notes, contact sheets, or other intermediate planning files into the user's requested output directory."
            ),
        ),
        PromptSection(
            name="environment_contract",
            content=(
                "You operate inside a thread-scoped runtime with typed state, ordered middleware, "
                "sandbox wiring, and registry-driven tool visibility."
            ),
        ),
        PromptSection(
            name="path_contract",
            content=(
                "Default working directory is /mnt/user-data/workspace. "
                "list_dir may start at /mnt/user-data for discovery, then continue inside "
                "/mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, "
                "or a configured bridge under /mnt/user-data/workspace/_host/<alias>. "
                "search_files, glob_files, and grep_files must target /mnt/user-data or one concrete virtual root and return only bounded virtual-path matches. "
                "read_file must target a listed concrete virtual root. "
                "write_file may create or overwrite files under /mnt/user-data/workspace, /mnt/user-data/outputs, or an explicitly configured writable bridge root. "
                "patch_file only edits existing UTF-8 text files under /mnt/user-data/workspace, /mnt/user-data/outputs, or an explicitly configured writable bridge root. "
                "Do not use '.', '/', or unlisted host paths. Do not infer host paths; only use runtime_path_roots bridge roots that are actually listed for this thread. "
                "Treat artifact URLs as presentation surfaces, not execution paths."
            ),
        ),
    ]
    if runtime_path_context:
        sections.append(PromptSection(name="runtime_path_roots", content=runtime_path_context))
    if project_context:
        sections.append(PromptSection(name="project_context_files", content=project_context))
    if memory_snapshot:
        sections.append(
            PromptSection(
                name="memory_snapshot",
                content=(
                    "Stable snapshot contains ambient defaults only: durable user preferences, profile facets, "
                    "workspace constraints, and reusable outcomes that passed memory governance. Treat it as prior context, "
                    "not as fresh user instruction. Use dynamic memory recall, session_search, or memory_trace for fact lookup "
                    "when the current task needs specific evidence or when the snapshot may be stale.\n\n"
                    f"{memory_snapshot}"
                ),
            )
        )
    sections.extend([
        PromptSection(name="capability_summary", content=capability_summary),
        PromptSection(
            name="deferred_capabilities",
            content=(
                f"{deferred_summary}\n\n"
                "Deferred capabilities are not callable until you inspect them through capability_search. "
                "Use capability_search when a needed capability is listed here but its callable schema is not yet visible. "
                "Use tool_catalog to browse registered capability metadata and tool_view to inspect a likely match. "
                "Do not create new tools or MCP servers from runtime prompt context."
            ),
        ),
        PromptSection(
            name="delegation_rules",
            content=(
                "If delegated-task capability is visible, delegation is bounded. "
                f"Maximum concurrent workers: {delegation_max_concurrency if delegation_max_concurrency is not None else 'runtime-defined'}. "
                f"Maximum delegation depth: {delegation_max_depth if delegation_max_depth is not None else 'runtime-defined'}. "
                "Never assume unlimited parallelism. Batch only independent delegated work; execute dependent tasks in order. "
                "When batching, call delegate_batch once with a real array/object, not a JSON-encoded string. "
                "After a batch submit, call subagent with action='join' once using the returned task_ids, then synthesize. "
                "Do not repeatedly poll list/status unless join timed out. Keep final synthesis in the lead agent, "
                "do not let children gain tools outside the parent-visible allowlist, "
                "and do not let workers ask the user directly or own final presentation. "
                "Delegated children inherit only the bounded stable memory snapshot in their prompt; do not assume they have live dynamic recall. "
                "For memory-heavy or cross-thread tasks, the parent should perform explicit recall/session_search and pass only the relevant evidence in the child prompt. "
                "When you know the child only needs a narrow subset, pass requested_tool_names explicitly. "
                "If the delegated child needs guarded actions like write_file, patch_file, or run_command, expect the parent turn to require approval before spawning. "
                "Never narrate internal delegation repair, startup, waiting, or polling status as user-visible final content."
            ),
        ),
        PromptSection(
            name="response_contract",
            content=(
                "Provide direct, execution-focused answers. "
                "If tools are unavailable, deferred, denied, or awaiting approval, explain the runtime constraint and adapt."
            ),
        ),
    ])
    return sections
