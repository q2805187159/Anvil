from __future__ import annotations

import hashlib
import json
import re
from uuid import uuid4
from pathlib import Path
from threading import Lock
from typing import Any

from .contracts import (
    CuratedEntry,
    CuratedStoreRepository,
    CuratedStoreState,
    CuratedStoreView,
    sanitize_memory_context_text,
    utc_now,
)
from .profile_facets import ProfileFacetPolicy, apply_profile_facet_budgets, build_profile_facet, entry_is_profile_visible
from .retention import record_access_metadata, retention_metrics
from .scrubber import MemorySecretScrubber
from anvil.config import MemoryPlatformStoreConfig
from anvil.runtime.token_budget import TokenBudgetService

from .resolution import MemoryResolutionService


def _entry_id(store_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{store_id}:{content}".encode("utf-8")).hexdigest()[:16]
    return f"{store_id}:{digest}"


def _recall_category_boost(category: str) -> float:
    return {
        "resolved_outcome": 0.35,
        "correction": 0.30,
        "project_constraint": 0.25,
        "workflow": 0.20,
    }.get(category, 0.0)


def _query_terms(value: str) -> tuple[str, ...]:
    normalized = value.lower().strip()
    if not normalized:
        return ()
    tokens = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)
    terms: list[str] = [token for token in tokens if token]
    chinese_chars = [token for token in tokens if len(token) == 1 and "\u4e00" <= token <= "\u9fff"]
    terms.extend("".join(chinese_chars[index : index + 2]) for index in range(max(len(chinese_chars) - 1, 0)))
    return tuple(dict.fromkeys(terms))


class JsonCuratedStoreRepository(CuratedStoreRepository):
    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path).expanduser().resolve()
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def load_store(self, store_id: str) -> CuratedStoreState:
        path = self.base_path / f"{store_id}.json"
        with self._lock:
            if not path.exists():
                raise FileNotFoundError(store_id)
            return CuratedStoreState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_store(self, state: CuratedStoreState) -> None:
        path = self.base_path / f"{state.store_id}.json"
        tmp_path = path.with_name(f"{path.stem}.{uuid4().hex}.json.tmp")
        with self._lock:
            tmp_path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
            tmp_path.replace(path)

    def list_store_ids(self) -> list[str]:
        with self._lock:
            if not self.base_path.exists():
                return []
            return sorted(path.stem for path in self.base_path.glob("*.json"))


class CuratedStoreManager:
    def __init__(
        self,
        *,
        store_configs: dict[str, MemoryPlatformStoreConfig],
        repository: CuratedStoreRepository,
        token_budget: TokenBudgetService | None = None,
        resolution_service: MemoryResolutionService | None = None,
        profile_facet_policy: ProfileFacetPolicy | None = None,
    ) -> None:
        self._store_configs = store_configs
        self._repository = repository
        self._token_budget = token_budget or TokenBudgetService()
        self._resolution_service = resolution_service or MemoryResolutionService()
        self._profile_facet_policy = profile_facet_policy or ProfileFacetPolicy()
        self._scrubber = MemorySecretScrubber()
        self._ensure_store_files()

    def list_stores(self) -> tuple[CuratedStoreView, ...]:
        return tuple(self._to_view(self._load_store(store_id)) for store_id in self._store_configs)

    def list_entries(self, store_id: str) -> tuple[CuratedEntry, ...]:
        return tuple(self._load_store(store_id).entries)

    def find_entry(self, entry_id_or_memory_id: str, *, include_inactive: bool = True) -> CuratedEntry | None:
        for store_id in self._store_configs:
            for entry in self._load_store(store_id).entries:
                if entry.entry_id != entry_id_or_memory_id and entry.memory_id != entry_id_or_memory_id:
                    continue
                if not include_inactive and self._is_inactive(entry):
                    return None
                return entry
        return None

    def entry_is_recall_visible(self, entry: CuratedEntry) -> bool:
        if self._is_inactive(entry):
            return False
        if entry.store_id == "user_profile":
            return self._entry_is_profile_visible_with_budget(entry)
        return True

    def create_entry(
        self,
        store_id: str,
        *,
        content: str,
        category: str = "note",
        source_kind: str = "manual",
        priority: float = 0.5,
        metadata: dict | None = None,
        memory_id: str | None = None,
        layer_id: str | None = None,
        thread_id: str | None = None,
        user_id: str | None = None,
        workspace_id: str | None = None,
        source_ref: str | None = None,
        confidence: float = 0.5,
        salience: float = 0.5,
        last_accessed_at=None,
        evidence_refs: tuple[str, ...] = (),
        supersedes: tuple[str, ...] = (),
        conflicts_with: tuple[str, ...] = (),
        expires_at=None,
        status: str = "active",
        write_policy: str = "manual",
        write_reason: str | None = None,
    ) -> CuratedEntry:
        state = self._load_store(store_id)
        now = utc_now()
        normalized = self._scrubber.scrub(content).text.strip()
        existing = next((entry for entry in state.entries if entry.content == normalized), None)
        if existing is not None:
            existing.updated_at = now
            self._save_store(state)
            return existing

        entry = CuratedEntry(
            entry_id=_entry_id(store_id, normalized),
            memory_id=memory_id or _entry_id(store_id, normalized),
            store_id=store_id,
            layer_id=layer_id,
            thread_id=thread_id,
            user_id=user_id,
            workspace_id=workspace_id,
            source_ref=source_ref,
            content=normalized,
            category=category,
            source_kind=source_kind,
            priority=priority,
            confidence=confidence,
            salience=salience,
            last_accessed_at=last_accessed_at,
            evidence_refs=evidence_refs,
            supersedes=supersedes,
            conflicts_with=conflicts_with,
            expires_at=expires_at,
            status=status,
            write_policy=write_policy,
            write_reason=write_reason,
            metadata=metadata or {},
            created_at=now,
            updated_at=now,
        )
        state.entries.append(entry)
        self._enforce_budget(state)
        self._save_store(state)
        return entry

    def update_entry(
        self,
        store_id: str,
        entry_id: str,
        *,
        content: str | None = None,
        category: str | None = None,
        priority: float | None = None,
        metadata: dict | None = None,
        confidence: float | None = None,
        salience: float | None = None,
        last_accessed_at=None,
        evidence_refs: tuple[str, ...] | None = None,
        supersedes: tuple[str, ...] | None = None,
        conflicts_with: tuple[str, ...] | None = None,
        expires_at=None,
        status: str | None = None,
        write_policy: str | None = None,
        write_reason: str | None = None,
    ) -> CuratedEntry:
        state = self._load_store(store_id)
        for entry in state.entries:
            if entry.entry_id != entry_id:
                continue
            if content is not None:
                entry.content = self._scrubber.scrub(content).text.strip()
            if category is not None:
                entry.category = category
            if priority is not None:
                entry.priority = priority
            if metadata is not None:
                entry.metadata = metadata
            if confidence is not None:
                entry.confidence = confidence
            if salience is not None:
                entry.salience = salience
            if last_accessed_at is not None:
                entry.last_accessed_at = last_accessed_at
            if evidence_refs is not None:
                entry.evidence_refs = evidence_refs
            if supersedes is not None:
                entry.supersedes = supersedes
            if conflicts_with is not None:
                entry.conflicts_with = conflicts_with
            if expires_at is not None:
                entry.expires_at = expires_at
            if status is not None:
                entry.status = status
            if write_policy is not None:
                entry.write_policy = write_policy
            if write_reason is not None:
                entry.write_reason = write_reason
            entry.updated_at = utc_now()
            self._enforce_budget(state)
            self._save_store(state)
            return entry
        raise KeyError(entry_id)

    def touch_entry(
        self,
        store_id: str,
        entry_id_or_memory_id: str,
        *,
        source: str = "recall",
        accessed_at=None,
    ) -> CuratedEntry:
        state = self._load_store(store_id)
        now = accessed_at or utc_now()
        for entry in state.entries:
            if entry.entry_id != entry_id_or_memory_id and entry.memory_id != entry_id_or_memory_id:
                continue
            entry.last_accessed_at = now
            entry.metadata = record_access_metadata(entry.metadata, source=source, now=now)
            metrics = retention_metrics(entry, now=now)
            entry.metadata["retention_score"] = metrics["retention_score"]
            entry.metadata["retention_tier"] = metrics["tier"]
            self._save_store(state)
            return entry
        raise KeyError(entry_id_or_memory_id)

    def update_summary(self, store_id: str, summary: str) -> CuratedStoreState:
        state = self._load_store(store_id)
        state.summary = self._truncate_summary(state, self._scrubber.scrub(summary).text)
        self._enforce_budget(state)
        self._save_store(state)
        return state

    def update_summary_sections(self, store_id: str, sections: dict[str, dict[str, Any]]) -> CuratedStoreState:
        state = self._load_store(store_id)
        normalized = _normalize_summary_sections(sections)
        if not normalized:
            return state
        merged = {key: dict(value) for key, value in state.summary_sections.items()}
        for section_id, values in normalized.items():
            current = dict(merged.get(section_id, {}))
            current.update(values)
            merged[section_id] = current
        state.summary_sections = merged
        state.summary = self._truncate_summary(state, _render_summary_sections(merged))
        self._enforce_budget(state)
        self._save_store(state)
        return state

    def delete_entry(self, store_id: str, entry_id: str) -> None:
        state = self._load_store(store_id)
        state.entries = [entry for entry in state.entries if entry.entry_id != entry_id]
        self._save_store(state)

    def search_entries(self, query: str, limit: int = 5) -> tuple[CuratedEntry, ...]:
        normalized = query.lower().strip()
        query_terms = _query_terms(normalized)
        scored: list[tuple[float, CuratedEntry]] = []
        for store_id in self._store_configs:
            state = self._load_store(store_id)
            for entry in state.entries:
                if not self.entry_is_recall_visible(entry):
                    continue
                haystack = f"{entry.category} {entry.content}".lower()
                if normalized not in haystack:
                    overlap = sum(1 for token in query_terms if token and token in haystack)
                    if overlap == 0:
                        continue
                    score = overlap / max(len(query_terms), 1)
                else:
                    score = 1.0
                scored.append((score + self._resolution_service.effective_score(entry) + _recall_category_boost(entry.category), entry))
        scored.sort(key=lambda item: item[0], reverse=True)
        return tuple(entry for _, entry in scored[:limit])

    def render_stable_snapshot(self) -> str:
        sections: list[str] = []
        for store_id in self._store_configs:
            state = self._load_store(store_id)
            rendered = self._render_store_for_prompt(state)
            if rendered:
                sections.append(rendered)
        return "\n\n".join(sections)

    def snapshot_fingerprint(self) -> str:
        payload = {
            store_id: self._load_store(store_id).model_dump(mode="json")
            for store_id in self._store_configs
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _ensure_store_files(self) -> None:
        for store_id, config in self._store_configs.items():
            path = self._repository.list_store_ids()
            if store_id in path:
                continue
            max_tokens, injection_tokens, budget_source = self._effective_budget_from_config(config)
            self._repository.save_store(
                CuratedStoreState(
                    store_id=store_id,
                    display_name=config.display_name or store_id.replace("_", " ").title(),
                    max_chars=config.max_chars,
                    injection_chars=config.injection_chars,
                    max_tokens=max_tokens,
                    injection_tokens=injection_tokens,
                    budget_source=budget_source,
                    category_bias=config.category_bias,
                )
            )

    def _load_store(self, store_id: str) -> CuratedStoreState:
        if store_id not in self._store_configs:
            raise KeyError(store_id)
        try:
            return self._reconcile_store(store_id, self._repository.load_store(store_id))
        except FileNotFoundError:
            config = self._store_configs[store_id]
            max_tokens, injection_tokens, budget_source = self._effective_budget_from_config(config)
            state = CuratedStoreState(
                store_id=store_id,
                display_name=config.display_name or store_id.replace("_", " ").title(),
                max_chars=config.max_chars,
                injection_chars=config.injection_chars,
                max_tokens=max_tokens,
                injection_tokens=injection_tokens,
                budget_source=budget_source,
                category_bias=config.category_bias,
            )
            self._repository.save_store(state)
            return state

    def _effective_budget_from_config(self, config: MemoryPlatformStoreConfig) -> tuple[int, int, str]:
        max_tokens = config.max_tokens if config.max_tokens and config.max_tokens > 0 else max(config.max_chars // 4, 1)
        injection_tokens = (
            config.injection_tokens
            if config.injection_tokens and config.injection_tokens > 0
            else max(config.injection_chars // 4, 1)
        )
        source = "config" if config.max_tokens and config.injection_tokens else "fallback"
        return int(max_tokens), int(injection_tokens), source

    def _reconcile_store(self, store_id: str, state: CuratedStoreState) -> CuratedStoreState:
        config = self._store_configs[store_id]
        max_tokens, injection_tokens, config_source = self._effective_budget_from_config(config)
        updates: dict[str, object] = {}
        migrated = False

        if not state.display_name:
            updates["display_name"] = config.display_name or store_id.replace("_", " ").title()
            migrated = True
        if state.max_chars <= 0:
            updates["max_chars"] = config.max_chars
            migrated = True
        if state.injection_chars <= 0:
            updates["injection_chars"] = config.injection_chars
            migrated = True
        if state.max_tokens is None or state.max_tokens <= 0:
            updates["max_tokens"] = max_tokens
            migrated = True
        if state.injection_tokens is None or state.injection_tokens <= 0:
            updates["injection_tokens"] = injection_tokens
            migrated = True
        if not state.category_bias:
            updates["category_bias"] = config.category_bias
            migrated = True

        if migrated:
            updates["budget_source"] = "migrated"
        elif state.budget_source not in {"config", "stored", "migrated", "fallback"}:
            updates["budget_source"] = config_source

        if not updates:
            return state
        updated = state.model_copy(update=updates)
        self._repository.save_store(updated)
        return updated

    def _save_store(self, state: CuratedStoreState) -> None:
        state.updated_at = utc_now()
        self._repository.save_store(state)

    def _enforce_budget(self, state: CuratedStoreState) -> None:
        while (
            self._usage_chars(state) > state.max_chars
            or (state.max_tokens is not None and self._usage_tokens(state) > state.max_tokens)
        ) and state.entries:
            active_entries = [entry for entry in state.entries if not self._is_inactive(entry)]
            pool = active_entries or list(state.entries)
            oldest = min(pool, key=lambda entry: (self._resolution_service.effective_score(entry), entry.updated_at))
            state.entries.remove(oldest)
            state.summary = self._merge_summary(state.summary, oldest.content, state.max_chars // 2)

    def _usage_chars(self, state: CuratedStoreState) -> int:
        joined = "\n".join(entry.content for entry in state.entries)
        return len(state.summary) + len(joined)

    def _usage_tokens(self, state: CuratedStoreState) -> int:
        joined = "\n".join(entry.content for entry in state.entries)
        return self._token_budget.count_text(state.summary) + self._token_budget.count_text(joined)

    def _merge_summary(self, summary: str, content: str, max_chars: int) -> str:
        merged = "\n".join(part for part in [summary.strip(), content.strip()] if part)
        return merged[:max_chars]

    def _render_store_for_prompt(self, state: CuratedStoreState) -> str:
        injection_tokens = state.injection_tokens or max(state.injection_chars // 4, 1)
        bits: list[str] = [
            f"[{state.display_name}] {self._usage_tokens(state)}/{state.max_tokens or max(state.max_chars // 4, 1)} tokens",
        ]
        if state.summary:
            bits.append(
                "summary: "
                + self._token_budget.truncate_text(
                    sanitize_memory_context_text(state.summary),
                    max_tokens=max(injection_tokens // 2, 1),
                    max_chars=state.injection_chars,
                )
            )
        remaining = injection_tokens - self._token_budget.count_text("\n".join(bits))
        if state.store_id == "user_profile":
            visible_ids = self._visible_profile_entry_ids(state.entries)
            entries = [entry for entry in state.entries if entry.entry_id in visible_ids]
        else:
            entries = [entry for entry in state.entries if not self._is_inactive(entry)]
        for entry in sorted(entries, key=lambda item: self._resolution_service.effective_score(item), reverse=True):
            line = f"- {sanitize_memory_context_text(entry.content)}"
            line_tokens = self._token_budget.count_text(line)
            if remaining - line_tokens < 0:
                break
            bits.append(line)
            remaining -= line_tokens
        return "\n".join(bits)

    def _to_view(self, state: CuratedStoreState) -> CuratedStoreView:
        rendered = self._render_store_for_prompt(state)
        effective_max_tokens = state.max_tokens or max(state.max_chars // 4, 1)
        effective_injection_tokens = state.injection_tokens or max(state.injection_chars // 4, 1)
        return CuratedStoreView(
            store_id=state.store_id,
            display_name=state.display_name,
            max_chars=state.max_chars,
            injection_chars=state.injection_chars,
            max_tokens=state.max_tokens,
            injection_tokens=state.injection_tokens,
            effective_max_tokens=effective_max_tokens,
            effective_injection_tokens=effective_injection_tokens,
            budget_source=state.budget_source,
            actual_injection_tokens=self._token_budget.count_text(rendered) if rendered else 0,
            actual_injection_chars=len(rendered),
            usage_chars=self._usage_chars(state),
            usage_tokens=self._usage_tokens(state),
            entry_count=len(state.entries),
            summary=state.summary,
            summary_sections=state.summary_sections,
            snapshot_status="frozen-capable",
            updated_at=state.updated_at,
        )

    def _truncate_summary(self, state: CuratedStoreState, summary: str) -> str:
        injection_tokens = state.injection_tokens or max(state.injection_chars // 4, 1)
        return self._token_budget.truncate_text(
            summary.strip(),
            max_tokens=max(injection_tokens, 1),
            max_chars=max(state.injection_chars, 1),
        )

    def _is_inactive(self, entry: CuratedEntry) -> bool:
        if entry.status in {"superseded", "rejected", "archived"}:
            return True
        return entry.expires_at is not None and entry.expires_at <= utc_now()

    def _entry_is_profile_visible_with_budget(self, entry: CuratedEntry) -> bool:
        if not entry_is_profile_visible(entry, policy=self._profile_facet_policy):
            return False
        state = self._load_store("user_profile")
        return entry.entry_id in self._visible_profile_entry_ids(state.entries)

    def _visible_profile_entry_ids(self, entries: list[CuratedEntry]) -> set[str]:
        facets = [
            build_profile_facet(entry, policy=self._profile_facet_policy)
            for entry in entries
            if not self._is_inactive(entry)
        ]
        facets.sort(key=lambda item: (item.class_id, -item.stability_score, item.key))
        budgeted = apply_profile_facet_budgets(tuple(facets), policy=self._profile_facet_policy)
        return {facet.entry_id for facet in budgeted if facet.prompt_visible}


def _normalize_summary_sections(sections: dict[str, dict[str, Any]]) -> dict[str, dict[str, str]]:
    normalized: dict[str, dict[str, str]] = {}
    for section_id, values in sections.items():
        section_key = str(section_id).strip()
        if not section_key or not isinstance(values, dict):
            continue
        items: dict[str, str] = {}
        for key, value in values.items():
            item_key = str(key).strip()
            if not item_key:
                continue
            text = MemorySecretScrubber().scrub(str(value)).text.strip()
            if text:
                items[item_key] = text
        if items:
            normalized[section_key] = items
    return normalized


def _render_summary_sections(sections: dict[str, dict[str, str]]) -> str:
    parts: list[str] = []
    for section_id in sorted(sections):
        values = sections[section_id]
        rendered_values = [f"{key}: {value}" for key, value in values.items() if value]
        if rendered_values:
            parts.append(f"{section_id}: " + " | ".join(rendered_values))
    return "\n".join(parts)
