from __future__ import annotations

CODING_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "code_map",
        "code_focus",
        "code_symbols",
        "code_symbol_search",
        "code_references",
        "code_definition",
        "code_semantic_index",
        "code_file_summary",
        "code_impact",
    }
)

CODING_AUDIT_TOOL_NAMES = frozenset(
    {
        "code_health",
        "code_security_scan",
        "code_pattern_scan",
        "code_doc_graph",
    }
)

CODING_TOOL_NAMES = CODING_DISCOVERY_TOOL_NAMES | CODING_AUDIT_TOOL_NAMES

CAPABILITY_DISCOVERY_TOOL_NAMES = frozenset(
    {
        "capability_search",
        "tool_catalog",
        "tool_view",
        "toolset_catalog",
        "toolset_view",
    }
)

PROCEDURE_NOISE_TOOL_NAMES = CAPABILITY_DISCOVERY_TOOL_NAMES | frozenset({"write_todos"})
