from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from .contracts import CuratedEntry, ProfileFacet, ProfileFacetPolicySnapshot, utc_now


PROFILE_FACET_CLASS_BUDGETS: dict[str, int] = {
    "style": 4,
    "identity": 4,
    "tooling": 5,
    "veto": 3,
    "goal": 3,
    "channel": 1,
    "workflow": 5,
    "environment": 5,
    "project_fact": 5,
    "overflow": 5,
}

PROFILE_FACET_REQUIRE_REVIEW_CLASSES = frozenset({"identity", "veto"})


@dataclass(frozen=True)
class ProfileFacetPolicy:
    active_threshold: float = 1.5
    provisional_threshold: float = 0.7
    candidate_threshold: float = 0.4
    require_review_classes: frozenset[str] = frozenset({"identity", "veto"})
    class_budgets: dict[str, int] | None = None
    default_class_budget: int = 5
    max_facets: int = 80
    pollution_requires_review: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "active_threshold", max(0.0, float(self.active_threshold)))
        object.__setattr__(
            self,
            "provisional_threshold",
            max(0.0, min(float(self.provisional_threshold), self.active_threshold)),
        )
        object.__setattr__(
            self,
            "candidate_threshold",
            max(0.0, min(float(self.candidate_threshold), self.provisional_threshold)),
        )
        object.__setattr__(
            self,
            "require_review_classes",
            frozenset(_normalize_class(item) for item in self.require_review_classes if _normalize_class(item)),
        )
        budgets: dict[str, int] = {}
        source = self.class_budgets if self.class_budgets is not None else PROFILE_FACET_CLASS_BUDGETS
        for raw_key, raw_value in source.items():
            key = _normalize_class(str(raw_key))
            try:
                value = int(raw_value)
            except (TypeError, ValueError):
                continue
            if key and value > 0:
                budgets[key] = min(value, 100)
        object.__setattr__(self, "class_budgets", budgets)
        object.__setattr__(self, "default_class_budget", max(1, min(int(self.default_class_budget), 100)))
        object.__setattr__(self, "max_facets", max(1, min(int(self.max_facets), 500)))

    def budget_for(self, class_id: str) -> int:
        return (self.class_budgets or {}).get(_normalize_class(class_id), self.default_class_budget)

    def snapshot(self) -> ProfileFacetPolicySnapshot:
        return ProfileFacetPolicySnapshot(
            active_threshold=self.active_threshold,
            provisional_threshold=self.provisional_threshold,
            candidate_threshold=self.candidate_threshold,
            require_review_classes=tuple(sorted(self.require_review_classes)),
            class_budgets=dict(sorted((self.class_budgets or {}).items())),
            default_class_budget=self.default_class_budget,
            max_facets=self.max_facets,
            pollution_requires_review=self.pollution_requires_review,
        )


def profile_facet_policy_from_config(config: Any | None) -> ProfileFacetPolicy:
    if config is None:
        return ProfileFacetPolicy()
    values = config.model_dump(mode="python") if hasattr(config, "model_dump") else dict(config)
    return ProfileFacetPolicy(
        active_threshold=values.get("active_threshold", 1.5),
        provisional_threshold=values.get("provisional_threshold", 0.7),
        candidate_threshold=values.get("candidate_threshold", 0.4),
        require_review_classes=frozenset(values.get("require_review_classes") or PROFILE_FACET_REQUIRE_REVIEW_CLASSES),
        class_budgets=dict(values.get("class_budgets") or PROFILE_FACET_CLASS_BUDGETS),
        default_class_budget=values.get("default_class_budget", 5),
        max_facets=values.get("max_facets", 80),
        pollution_requires_review=bool(values.get("pollution_requires_review", True)),
    )


def apply_profile_facet_budgets(
    facets: tuple[ProfileFacet, ...],
    *,
    policy: ProfileFacetPolicy | None = None,
) -> tuple[ProfileFacet, ...]:
    effective_policy = policy or ProfileFacetPolicy()
    counts: dict[str, int] = {}
    budgeted: list[ProfileFacet] = []
    for facet in facets[: effective_policy.max_facets]:
        if facet.state != "active" or facet.user_state == "pinned":
            budgeted.append(facet)
            continue
        next_count = counts.get(facet.class_id, 0) + 1
        counts[facet.class_id] = next_count
        if next_count <= effective_policy.budget_for(facet.class_id):
            budgeted.append(facet)
            continue
        budgeted.append(
            facet.model_copy(
                update={
                    "state": "provisional",
                    "prompt_visible": False,
                    "reason": f"class budget exceeded for {facet.class_id}; pin this facet or raise the class budget",
                }
            )
        )
    return tuple(budgeted)


def profile_facet_id(memory_id: str, class_id: str, key: str) -> str:
    digest = hashlib.sha256(f"{memory_id}:{class_id}:{key}".encode("utf-8")).hexdigest()[:16]
    return f"profile-facet-{digest}"


def facet_class_for_entry(entry: CuratedEntry) -> str:
    explicit = _metadata_text(entry.metadata, "profile_class")
    if explicit:
        return _normalize_class(explicit)
    category = str(entry.category or "").strip().lower()
    if category in {"style", "tone", "format", "preference", "communication"}:
        return "style"
    if category in {"identity", "bio", "personal_context", "personal"}:
        return "identity"
    if category in {"tooling", "tool", "tool_preference"}:
        return "tooling"
    if category in {"veto", "forbidden", "avoid", "correction"}:
        return "veto"
    if category in {"goal", "objective"}:
        return "goal"
    if category in {"channel", "notification"}:
        return "channel"
    if category in {"workflow", "process"}:
        return "workflow"
    if category in {"environment", "env"}:
        return "environment"
    if category in {"project_fact", "project_context"}:
        return "project_fact"
    return "overflow"


def profile_facet_key_for_entry(entry: CuratedEntry, class_id: str) -> str:
    explicit = _metadata_text(entry.metadata, "profile_key")
    if explicit:
        return explicit[:80]
    normalized = " ".join(entry.content.lower().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{class_id}:{digest}"


def build_profile_facet(
    entry: CuratedEntry,
    *,
    existing: ProfileFacet | None = None,
    policy: ProfileFacetPolicy | None = None,
) -> ProfileFacet:
    effective_policy = policy or ProfileFacetPolicy()
    memory_id = entry.memory_id or entry.entry_id
    class_id = facet_class_for_entry(entry)
    key = profile_facet_key_for_entry(entry, class_id)
    user_state = _profile_user_state(entry, existing=existing)
    stability_score = profile_stability_score(entry)
    source_polluted = _source_polluted(entry)
    state, reason = profile_facet_state(
        stability_score=stability_score,
        class_id=class_id,
        user_state=user_state,
        source_polluted=source_polluted,
        policy=effective_policy,
    )
    prompt_visible = state == "active"
    now = utc_now()
    return ProfileFacet(
        facet_id=existing.facet_id if existing is not None else profile_facet_id(memory_id, class_id, key),
        source_memory_id=memory_id,
        entry_id=entry.entry_id,
        store_id=entry.store_id,
        class_id=class_id,
        key=key,
        value=entry.content,
        source_category=entry.category,
        evidence_refs=entry.evidence_refs,
        confidence=round(float(entry.confidence or 0.0), 4),
        salience=round(float(entry.salience or 0.0), 4),
        priority=round(float(entry.priority or 0.0), 4),
        stability_score=stability_score,
        state=state,
        user_state=user_state,
        prompt_visible=prompt_visible,
        source_polluted=source_polluted,
        pollution_reasons=_pollution_reasons(entry),
        reason=reason,
        last_seen_at=entry.last_accessed_at or entry.updated_at,
        created_at=existing.created_at if existing is not None else entry.created_at,
        updated_at=now,
    )


def profile_stability_score(entry: CuratedEntry) -> float:
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    explicit = metadata.get("profile_stability")
    if explicit is not None:
        try:
            return round(max(0.0, float(explicit)), 4)
        except (TypeError, ValueError):
            pass
    evidence_boost = min(len(entry.evidence_refs), 3) * 0.12
    access_boost = min(int(metadata.get("access_count") or 0), 5) * 0.04
    base = float(entry.confidence or 0.0) + float(entry.salience or 0.0) + float(entry.priority or 0.0) * 0.25
    return round(max(0.0, base + evidence_boost + access_boost), 4)


def profile_facet_state(
    *,
    stability_score: float,
    class_id: str,
    user_state: str,
    source_polluted: bool = False,
    policy: ProfileFacetPolicy | None = None,
) -> tuple[str, str]:
    effective_policy = policy or ProfileFacetPolicy()
    if user_state == "forgotten":
        return "dropped", "user marked facet forgotten"
    if user_state == "pinned":
        return "active", "user pinned facet"
    if source_polluted and effective_policy.pollution_requires_review:
        return "provisional", "source thread used external/web/MCP context; requires explicit review before active prompt injection"
    if class_id in effective_policy.require_review_classes and stability_score < effective_policy.active_threshold:
        return "provisional", "class requires review before active prompt injection"
    if stability_score >= effective_policy.active_threshold:
        return "active", "stability score reached active threshold"
    if stability_score >= effective_policy.provisional_threshold:
        return "provisional", "stability score reached provisional threshold"
    if stability_score >= effective_policy.candidate_threshold:
        return "candidate", "stability score reached candidate threshold"
    return "dropped", "stability score below candidate threshold"


def entry_is_profile_visible(entry: CuratedEntry, *, policy: ProfileFacetPolicy | None = None) -> bool:
    if entry.store_id != "user_profile":
        return True
    if entry.status in {"superseded", "rejected", "archived"}:
        return False
    facet = build_profile_facet(entry, policy=policy)
    return facet.prompt_visible


def apply_profile_user_state(entry: CuratedEntry, user_state: str) -> dict[str, Any]:
    metadata = dict(entry.metadata or {})
    profile_meta = dict(metadata.get("profile_facet") or {})
    profile_meta["user_state"] = user_state
    metadata["profile_facet"] = profile_meta
    return metadata


def _profile_user_state(entry: CuratedEntry, *, existing: ProfileFacet | None) -> str:
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    nested = metadata.get("profile_facet")
    candidates = []
    if isinstance(nested, dict):
        candidates.append(nested.get("user_state"))
    candidates.append(metadata.get("profile_user_state"))
    if existing is not None:
        candidates.append(existing.user_state)
    for value in candidates:
        normalized = str(value or "").strip().lower()
        if normalized in {"auto", "pinned", "forgotten"}:
            return normalized
    return "auto"


def _source_polluted(entry: CuratedEntry) -> bool:
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    nested = metadata.get("profile_facet")
    if isinstance(nested, dict) and bool(nested.get("source_polluted")):
        return True
    return bool(metadata.get("source_polluted"))


def _pollution_reasons(entry: CuratedEntry) -> tuple[str, ...]:
    metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
    nested = metadata.get("profile_facet")
    values: list[Any] = []
    if isinstance(nested, dict):
        values.append(nested.get("pollution_reasons"))
    values.append(metadata.get("pollution_reasons"))
    reasons: list[str] = []
    for value in values:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list | tuple):
            candidates = list(value)
        else:
            candidates = []
        for item in candidates:
            text = str(item or "").strip()
            if text and text not in reasons:
                reasons.append(text[:160])
    return tuple(reasons[:5])


def _metadata_text(metadata: dict[str, Any], key: str) -> str:
    value = metadata.get(key)
    if value is None:
        nested = metadata.get("profile_facet")
        if isinstance(nested, dict):
            value = nested.get(key.removeprefix("profile_"))
    text = str(value or "").strip()
    return text


def _normalize_class(value: str) -> str:
    normalized = "".join(char.lower() if char.isalnum() else "_" for char in value.strip())
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = normalized.strip("_")
    return normalized or "overflow"
