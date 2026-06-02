from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from langchain_core.tools import StructuredTool

from anvil.runtime.tool_registry.contracts import (
    SchemaSanitizerDiagnostics,
    ToolRegistryEntry,
    ToolSourceKind,
    TypedApprovalPolicy,
    sanitize_tool_input_schema,
)


def _structured_tool_handler(*, name: str, description: str, func, input_schema: dict[str, object]) -> StructuredTool:
    clean_schema = sanitize_tool_input_schema(input_schema, diagnostics=SchemaSanitizerDiagnostics())
    return StructuredTool(name=name, description=description, func=func, args_schema=clean_schema)


class DelegationToolFactory:
    def __init__(self, *, subagent_service) -> None:
        self.subagent_service = subagent_service

    def build_tools(
        self,
        *,
        config_result,
        thread_id: str,
        parent_visible_tool_names: tuple[str, ...],
        execution_mode,
        feature_set,
        parent_run_id: str | None,
        trace_id: str | None,
    ) -> list[ToolRegistryEntry]:
        delegate_batch_description = (
            "Delegate several bounded tasks in one batch and return task descriptors. "
            "Prefer tasks=[{prompt,...}] or prompts=[...]."
        )

        def delegate_batch(
            prompts: Any = None,
            tasks: Any = None,
            requested_tool_names: Any = None,
        ) -> str:
            normalized = _normalize_delegation_items(
                prompts=prompts,
                tasks=tasks,
                requested_tool_names=requested_tool_names,
            )
            if "error" in normalized:
                return json.dumps(normalized, ensure_ascii=False)
            items = normalized["items"]
            max_batch_size = max(int(config_result.effective_config.subagents.max_concurrency), 1)
            if len(items) > max_batch_size:
                return json.dumps(
                    {
                        "error": f"delegate_batch received {len(items)} tasks but the current max_concurrency is {max_batch_size}",
                        "hint": "Split dependent or excess work into smaller batches. Only independent tasks should run together.",
                    },
                    ensure_ascii=False,
                )
            batch_id = f"delegate-batch-{uuid4().hex[:10]}"
            task_descriptors = []
            task_ids_by_key: dict[str, str] = {}
            batch_keys = {str(item.get("key")) for item in items if item.get("key")}
            seen_keys: set[str] = set()
            for item in items:
                dependency_task_ids = []
                for dependency in item.get("depends_on_task_ids") or ():
                    dependency_value = str(dependency)
                    if dependency_value in batch_keys and dependency_value not in seen_keys:
                        return json.dumps(
                            {
                                "error": f"delegate_batch task depends_on '{dependency_value}' before that batch key has been submitted",
                                "hint": "List dependency tasks before dependents, or depend on an existing task_id.",
                            },
                            ensure_ascii=False,
                        )
                    dependency_task_ids.append(task_ids_by_key.get(dependency_value, dependency_value))
                task = self.subagent_service.submit(
                    parent_thread_id=thread_id,
                    parent_run_id=parent_run_id,
                    prompt=item["prompt"],
                    parent_visible_tool_names=parent_visible_tool_names,
                    config_result=config_result,
                    requested_tool_names=tuple(item.get("requested_tool_names") or ()),
                    depends_on_task_ids=tuple(dependency_task_ids),
                    parent_delegation_depth=0,
                    trace_id=trace_id,
                    execution_mode=execution_mode,
                    batch_id=batch_id,
                    prompt_preview=_preview_text(item["prompt"]),
                )
                if item.get("key"):
                    key = str(item["key"])
                    task_ids_by_key[key] = task.task_id
                    seen_keys.add(key)
                task_descriptors.append(
                    {
                        "task_id": task.task_id,
                        "batch_id": batch_id,
                        "status": task.status.value,
                        "prompt_preview": task.prompt_preview,
                        "allowed_tool_names": list(task.allowed_tool_names),
                        "depends_on_task_ids": list(task.depends_on_task_ids),
                        "child_thread_id": task.child_thread_id,
                        "child_run_id": task.child_run_id,
                    }
                )
            return json.dumps(
                {
                    "batch_id": batch_id,
                    "tasks": task_descriptors,
                    "items": task_descriptors,
                    "join_hint": "Call subagent with action='join' and task_ids from this response once, then synthesize the final answer.",
                },
                ensure_ascii=False,
            )

        delegate_status_description = "Inspect delegated subagent task status for one task or all active tasks."

        def delegate_status(task_id: str | None = None, active_only: bool = False) -> str:
            if task_id:
                task = self.subagent_service.get_task(task_id)
                if task is None or task.parent_thread_id != thread_id:
                    return json.dumps({"error": f"unknown subagent task: {task_id}"}, ensure_ascii=False)
                return json.dumps(self.subagent_service._serialize_task(task), ensure_ascii=False)
            tasks = self.subagent_service.list_active_tasks(parent_thread_id=thread_id) if active_only else self.subagent_service.list_tasks(parent_thread_id=thread_id)
            return json.dumps([self.subagent_service._serialize_task(task) for task in tasks], ensure_ascii=False)

        delegate_cancel_description = "Cancel one delegated subagent task by task_id."

        def delegate_cancel(task_id: str) -> str:
            task = self.subagent_service.cancel(task_id, reason="cancelled by delegate_cancel")
            return json.dumps(self.subagent_service._serialize_task(task), ensure_ascii=False)

        approval = TypedApprovalPolicy(mode="runtime", risk_category="delegation")
        delegate_batch_schema = {
            "type": "object",
            "properties": {
                "prompts": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                        {"type": "object"},
                    ]
                },
                "tasks": {
                    "oneOf": [
                        {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "prompt": {"type": "string"},
                                    "requested_tool_names": {"type": "array", "items": {"type": "string"}},
                                    "profile": {"type": "string"},
                                    "expected_output": {"type": "string"},
                                    "timeout_seconds": {"type": "integer"},
                                    "key": {"type": "string"},
                                    "id": {"type": "string"},
                                    "depends_on": {"type": "array", "items": {"type": "string"}},
                                    "depends_on_task_ids": {"type": "array", "items": {"type": "string"}},
                                },
                                "required": ["prompt"],
                            },
                        },
                        {"type": "string"},
                        {"type": "object"},
                    ],
                },
                "requested_tool_names": {
                    "oneOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                        {"type": "null"},
                    ]
                },
            },
        }
        delegate_status_schema = {
            "type": "object",
            "properties": {
                "task_id": {"type": ["string", "null"]},
                "active_only": {"type": "boolean"},
            },
        }
        delegate_cancel_schema = {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        }
        return [
            ToolRegistryEntry(
                name="delegate_batch",
                display_name="Delegate Batch",
                source_kind=ToolSourceKind.BUILTIN,
                source_id="subagents",
                capability_group="delegation",
                summary="Submit several delegated jobs in one call.",
                handler=_structured_tool_handler(
                    name="delegate_batch",
                    description=delegate_batch_description,
                    func=delegate_batch,
                    input_schema=delegate_batch_schema,
                ),
                input_schema=delegate_batch_schema,
                risk_category="delegation",
                typed_approval=approval,
            ),
            ToolRegistryEntry(
                name="delegate_status",
                display_name="Delegate Status",
                source_kind=ToolSourceKind.BUILTIN,
                source_id="subagents",
                capability_group="delegation",
                summary="Inspect batch-style delegated task status.",
                handler=_structured_tool_handler(
                    name="delegate_status",
                    description=delegate_status_description,
                    func=delegate_status,
                    input_schema=delegate_status_schema,
                ),
                input_schema=delegate_status_schema,
            ),
            ToolRegistryEntry(
                name="delegate_cancel",
                display_name="Delegate Cancel",
                source_kind=ToolSourceKind.BUILTIN,
                source_id="subagents",
                capability_group="delegation",
                summary="Cancel one delegated task.",
                handler=_structured_tool_handler(
                    name="delegate_cancel",
                    description=delegate_cancel_description,
                    func=delegate_cancel,
                    input_schema=delegate_cancel_schema,
                ),
                input_schema=delegate_cancel_schema,
            ),
        ]


def _normalize_delegation_items(*, prompts: object, tasks: object, requested_tool_names: object) -> dict[str, object]:
    global_tools = _normalize_string_list(requested_tool_names)
    raw_tasks = _maybe_json(tasks)
    raw_prompts = _maybe_json(prompts)
    if raw_tasks is None and isinstance(raw_prompts, dict):
        raw_tasks = raw_prompts.get("tasks")
        raw_prompts = raw_prompts.get("prompts")
    if isinstance(raw_tasks, dict):
        raw_tasks = raw_tasks.get("tasks") or raw_tasks.get("prompts")

    source = raw_tasks if raw_tasks is not None else raw_prompts
    if isinstance(source, str):
        source = [source]
    if not isinstance(source, list):
        return {
            "error": "delegate_batch requires tasks=[{prompt: ...}] or prompts=[...]",
            "received_type": type(source).__name__,
        }

    items: list[dict[str, object]] = []
    seen_keys: set[str] = set()
    for index, raw_item in enumerate(source):
        task_tools = global_tools
        task_key: str | None = None
        dependency_task_ids: list[str] = []
        if isinstance(raw_item, dict):
            prompt = str(raw_item.get("prompt") or raw_item.get("content") or raw_item.get("description") or "").strip()
            task_tools = _normalize_string_list(raw_item.get("requested_tool_names")) or global_tools
            raw_key = raw_item.get("key") or raw_item.get("id")
            task_key = str(raw_key).strip() if raw_key is not None else None
            if task_key:
                if task_key in seen_keys:
                    return {"error": f"delegate_batch task key '{task_key}' is duplicated"}
                seen_keys.add(task_key)
            dependency_task_ids = _normalize_string_list(
                raw_item.get("depends_on_task_ids") or raw_item.get("depends_on")
            )
        else:
            prompt = str(raw_item or "").strip()
        if not prompt:
            return {"error": f"delegate_batch task at index {index} is missing a non-empty prompt"}
        items.append(
            {
                "prompt": prompt,
                "requested_tool_names": task_tools,
                "key": task_key,
                "depends_on_task_ids": dependency_task_ids,
            }
        )
    if not items:
        return {"error": "delegate_batch received no tasks"}
    return {"items": items}


def _maybe_json(value: object) -> object:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped:
        return None
    if stripped[0] not in "[{":
        return stripped
    try:
        return json.loads(stripped)
    except Exception:
        return stripped


def _normalize_string_list(value: object) -> list[str]:
    parsed = _maybe_json(value)
    if parsed is None:
        return []
    if isinstance(parsed, str):
        return [parsed] if parsed.strip() else []
    if isinstance(parsed, list):
        return [str(item).strip() for item in parsed if str(item).strip()]
    if isinstance(parsed, tuple):
        return [str(item).strip() for item in parsed if str(item).strip()]
    return []


def _preview_text(text: str, *, limit: int = 96) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"
