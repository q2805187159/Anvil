from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .contracts import CapabilityCatalogEntry, CapabilityBundle
from .registry import ToolRegistry

SEPARATOR_RE = re.compile(r"[-_/]+")


@dataclass(frozen=True)
class CapabilityCatalogMatchTrace:
    matched_fields: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, list[str]]:
        return {
            "matched_fields": list(self.matched_fields),
            "query_terms": list(self.query_terms),
        }


class CapabilityCatalogService:
    def list_entries(
        self,
        *,
        registry: ToolRegistry,
        bundle: CapabilityBundle,
        query: str | None = None,
        source_kind: str | None = None,
        capability_group: str | None = None,
    ) -> tuple[CapabilityCatalogEntry, ...]:
        normalized_query = self._normalize_search_text(query or "")
        query_terms = self._query_terms(normalized_query)
        items = registry.catalog_entries(bundle)
        filtered: list[CapabilityCatalogEntry] = []
        for entry in items:
            if source_kind and entry.source_kind.value != source_kind:
                continue
            if capability_group and entry.capability_group != capability_group:
                continue
            if normalized_query:
                if not query_terms:
                    continue
                if not self._matching_fields(entry, normalized_query, query_terms):
                    continue
            filtered.append(entry)
        return tuple(filtered)

    def explain_matches(
        self,
        *,
        entries: tuple[CapabilityCatalogEntry, ...],
        query: str | None = None,
    ) -> dict[str, CapabilityCatalogMatchTrace]:
        normalized_query = self._normalize_search_text(query or "")
        if not normalized_query:
            return {}
        query_terms = self._query_terms(normalized_query)
        traces: dict[str, CapabilityCatalogMatchTrace] = {}
        for entry in entries:
            matched_fields = self._matching_fields(entry, normalized_query, query_terms)
            if matched_fields:
                traces[entry.name] = CapabilityCatalogMatchTrace(
                    matched_fields=matched_fields,
                    query_terms=query_terms,
                )
        return traces

    def get_entry(
        self,
        *,
        registry: ToolRegistry,
        bundle: CapabilityBundle,
        name_or_capability_id: str,
    ) -> CapabilityCatalogEntry | None:
        for entry in registry.catalog_entries(bundle):
            if entry.name == name_or_capability_id or entry.capability_id == name_or_capability_id:
                return entry
        return None

    def _matching_fields(
        self,
        entry: CapabilityCatalogEntry,
        normalized_query: str,
        query_terms: tuple[str, ...],
    ) -> tuple[str, ...]:
        return tuple(
            field_name
            for field_name, value in self._search_fields(entry).items()
            if normalized_query in value or (query_terms and all(term in value for term in query_terms))
        )

    def _search_fields(self, entry: CapabilityCatalogEntry) -> dict[str, str]:
        fields = {
            "name": self._join_search_values([entry.name, entry.display_name]),
            "summary": self._join_search_values([entry.summary]),
            "source": self._join_search_values([entry.source_id, entry.capability_group]),
            "provenance": self._join_search_values(
                [json.dumps(entry.provenance, ensure_ascii=False, sort_keys=True)]
            ),
        }
        resource_values: list[object] = []
        for resource in entry.resources:
            resource_values.extend([resource.resource_id, resource.title, resource.description])
            resource_values.append(json.dumps(resource.metadata, ensure_ascii=False, sort_keys=True))
        fields["resources"] = self._join_search_values(resource_values)

        prompt_values: list[object] = []
        for prompt in entry.prompts:
            prompt_values.extend([prompt.prompt_id, prompt.title, prompt.description])
            prompt_values.append(json.dumps(prompt.metadata, ensure_ascii=False, sort_keys=True))
        fields["prompts"] = self._join_search_values(prompt_values)
        return fields

    def _search_text(self, entry: CapabilityCatalogEntry) -> str:
        return " ".join(self._search_fields(entry).values())

    def _join_search_values(self, values: list[object]) -> str:
        return self._normalize_search_text(" ".join(str(value) for value in values if value is not None))

    def _normalize_search_text(self, value: str) -> str:
        normalized = SEPARATOR_RE.sub(" ", value.lower())
        for source, target in {
            "generation": "generate",
            "generator": "generate",
            "generated": "generate",
            "generating": "generate",
        }.items():
            normalized = normalized.replace(source, target)
        return " ".join(normalized.split())

    def _query_terms(self, normalized_query: str) -> tuple[str, ...]:
        return tuple(term for term in normalized_query.replace(":", " ").split() if term)
