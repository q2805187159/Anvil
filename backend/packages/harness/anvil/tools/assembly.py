from __future__ import annotations

import base64
import json
import mimetypes
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from langchain_core.tools import BaseTool, StructuredTool

from anvil.config import CodeSemanticsConfig, DocumentsConfig, UploadsConfig
from anvil.documents import export_document as export_document_file
from anvil.documents import extract_document as extract_document_file
from anvil.memory.tools import build_memory_tools
from anvil.processes.service import DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS, MAX_PROCESS_WAIT_TIMEOUT_SECONDS
from anvil.scheduled_tasks import ScheduledTaskCreateRequest, ScheduledTaskUpdateRequest
from anvil.runtime.tool_registry.contracts import (
    CapabilityBundle,
    SchemaSanitizerDiagnostics,
    ToolRegistryEntry,
    ToolSourceKind,
    sanitize_tool_input_schema,
)
from anvil.runtime.tool_registry.registry import ToolRegistry
from anvil.runtime.tool_registry.tool_names import CODING_TOOL_NAMES
from anvil.tools.code_map import (
    build_code_definition,
    build_code_doc_graph,
    build_code_file_summary,
    build_code_focus,
    build_code_health,
    build_code_impact,
    build_code_map,
    build_code_pattern_scan,
    build_code_references,
    build_code_security_scan,
    build_code_semantic_index,
    build_code_symbol_search,
    build_code_symbols,
)
from anvil.tools.file_search import search_runtime_files


PYTHON_VIRTUAL_PATH_SHIM_DIR = Path(__file__).resolve().parents[1] / "sandbox" / "python_virtual_path_shim"
IMAGE_TOOL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_TOOL_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_VIEW_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_FOREGROUND_COMMAND_TIMEOUT_SECONDS = 120
MAX_FOREGROUND_COMMAND_TIMEOUT_SECONDS = 900


@dataclass
class CommandExecutionResult:
    command_id: str
    status: str
    exit_code: int | None
    stdout: str
    started_at: str
    completed_at: str | None = None


def _runtime_structured_tool_handler(*, tool_obj, name: str, input_schema: dict[str, object]):
    if isinstance(tool_obj, BaseTool):
        return tool_obj
    description = getattr(tool_obj, "description", None) or name
    if not input_schema:
        return StructuredTool.from_function(func=tool_obj, name=name, description=description)
    clean_schema = sanitize_tool_input_schema(input_schema, diagnostics=SchemaSanitizerDiagnostics())
    func = getattr(tool_obj, "func", None) or tool_obj
    return StructuredTool(name=name, description=description, func=func, args_schema=clean_schema)


def _runtime_tool_input_schema(tool_obj, metadata: dict[str, object]) -> dict[str, object]:
    configured = metadata.get("input_schema")
    if isinstance(configured, dict):
        return configured
    args_schema = getattr(tool_obj, "args_schema", None)
    return args_schema if isinstance(args_schema, dict) else {}


def _image_mime_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix not in IMAGE_TOOL_EXTENSIONS:
        return None
    guessed = mimetypes.guess_type(path.name)[0]
    if guessed in IMAGE_TOOL_MIME_TYPES:
        return guessed
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return None


def _bounded_timeout_seconds(
    requested: int | None,
    *,
    default_seconds: int,
    max_seconds: int,
) -> int:
    if requested is None:
        return default_seconds
    return min(max(int(requested), 0), max_seconds)


def _subprocess_output_text(*parts: object) -> str:
    chunks: list[str] = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, bytes):
            chunks.append(part.decode("utf-8", errors="replace"))
            continue
        chunks.append(str(part))
    return "".join(chunks)


def tool(*, description: str):
    def decorator(func):
        func.description = description
        return func

    return decorator


def assemble_runtime_tools(
    *,
    sandbox_provider,
    path_service,
    thread_id: str,
    memory_manager=None,
    process_service=None,
    scheduled_task_service=None,
    uploads_config: UploadsConfig | None = None,
    documents_config: DocumentsConfig | None = None,
    code_semantics_config: CodeSemanticsConfig | None = None,
    effective_config_fingerprint: str,
    vision_enabled: bool = True,
) -> tuple[ToolRegistry, CapabilityBundle]:
    registry = ToolRegistry()
    command_history: dict[str, CommandExecutionResult] = {}
    uploads_config = uploads_config or UploadsConfig()
    documents_config = documents_config or DocumentsConfig()
    code_semantics_config = code_semantics_config or CodeSemanticsConfig()
    filesystem_tool_names = {
        "read_file",
        "file_info",
        "write_file",
        "patch_file",
        "delete_path",
        "move_path",
        "make_dir",
        "list_dir",
        "search_files",
        "glob_files",
        "grep_files",
        "extract_document",
        "export_document",
    }
    if vision_enabled:
        filesystem_tool_names.add("view_image")

    def acquire_handle():
        return sandbox_provider.acquire(thread_id=thread_id, path_service=path_service)

    def build_command_env() -> dict[str, str]:
        env = dict(os.environ)
        if os.name == "nt":
            env.setdefault("SystemRoot", os.environ.get("SystemRoot", r"C:\Windows"))
            env.setdefault("ComSpec", os.environ.get("ComSpec", r"C:\Windows\System32\cmd.exe"))
        pythonpath = [str(PYTHON_VIRTUAL_PATH_SHIM_DIR)]
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env.update(
            {
                "ANVIL_WORKSPACE": str(path_service.thread_workspace_dir(thread_id)),
                "ANVIL_UPLOADS": str(path_service.thread_uploads_dir(thread_id)),
                "ANVIL_OUTPUTS": str(path_service.thread_outputs_dir(thread_id)),
                "ANVIL_SCRATCH": str(path_service.thread_scratch_dir(thread_id)),
                "ANVIL_VIRTUAL_PATH_MAP": json.dumps(path_service.virtual_path_map(thread_id), ensure_ascii=False),
                "PYTHONPATH": os.pathsep.join(pythonpath),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        return env

    def translate_command(command: str) -> str:
        handle = acquire_handle()
        if process_service is not None:
            capabilities = process_service.capabilities()
            if capabilities.remote or capabilities.isolated:
                return command
        if getattr(handle, "provider_mode", "local") == "isolated":
            return command
        translated = path_service.translate_runtime_text_to_host(command, thread_id=thread_id)
        return translated or command

    def process_backend_uses_runtime_paths() -> bool:
        if process_service is None:
            return False
        capabilities = process_service.capabilities()
        return bool(capabilities.remote or capabilities.isolated)

    def ensure_command_cwd_allowed(host_cwd: Path, projection) -> None:
        path_service.ensure_within_any_allowed_root(thread_id, host_cwd, projection.policy_roots)

    def translate_runtime_path(path: str) -> str:
        translated = path_service.translate_runtime_text_to_virtual(path, thread_id=thread_id)
        return translated or path

    def translate_runtime_output(text: str | None) -> str:
        translated = path_service.translate_runtime_text_to_virtual(text, thread_id=thread_id)
        return translated or ""

    def translate_runtime_payload(value):
        translated = path_service.translate_runtime_data_to_virtual(value, thread_id=thread_id)
        return translated if translated is not None else value

    def _translate_paths_in_payload(value):
        return translate_runtime_payload(value)

    def output_artifact_url(virtual_path: str | None) -> str | None:
        if not isinstance(virtual_path, str) or not virtual_path.startswith("/mnt/user-data/outputs/"):
            return None
        relative_path = virtual_path.removeprefix("/mnt/user-data/outputs/")
        return path_service.to_artifact_descriptor(thread_id, "outputs", relative_path).artifact_url

    def with_output_artifact_urls(payload: dict[str, object], mapping: dict[str, str]) -> dict[str, object]:
        enriched = dict(payload)
        for source_key, target_key in mapping.items():
            artifact_url = output_artifact_url(enriched.get(source_key) if isinstance(enriched.get(source_key), str) else None)
            if artifact_url:
                enriched[target_key] = artifact_url
        return enriched

    def _code_project_path(path: str = "/mnt/user-data/workspace", project_path: str | None = None) -> str:
        selected = (project_path or path or "/mnt/user-data/workspace").strip()
        path_service.resolve_virtual_path(thread_id, selected)
        return selected

    def numbered_lines(content: str, *, start_line: int) -> str:
        if not content:
            return content
        lines = content.splitlines(keepends=True)
        return "".join(f"{start_line + index}: {line}" for index, line in enumerate(lines))

    @tool(
        description=(
            "Read a UTF-8 text file from an absolute virtual runtime path under "
            "/mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, "
            "or a configured bridge such as /mnt/user-data/workspace/_host/<alias>. "
            "Use start_line, max_lines, or max_chars for large files; structured=true returns line and truncation metadata. "
            "Set numbered=true to prefix returned lines with stable 1-based line numbers before patching. "
            "PDF files are also supported here: read_file will extract text from the PDF when possible. "
            "Do not use /mnt/user-data itself, '.', '/', or unlisted host paths."
        )
    )
    def read_file(
        path: str,
        start_line: int = 1,
        max_lines: int | None = None,
        max_chars: int | None = None,
        structured: bool = False,
        numbered: bool = False,
    ) -> str:
        if structured or start_line != 1 or max_lines is not None or max_chars is not None:
            payload = acquire_handle().read_file_window(
                path,
                start_line=start_line,
                max_lines=max_lines,
                max_chars=max_chars,
            )
            if numbered:
                payload = {**payload, "content": numbered_lines(str(payload.get("content", "")), start_line=int(payload["start_line"]))}
            return json.dumps(payload, ensure_ascii=False)
        content = acquire_handle().read_file(path)
        return numbered_lines(content, start_line=1) if numbered else content

    @tool(
        description=(
            "View one PNG, JPEG, WEBP, or GIF image from a virtual path under "
            "/mnt/user-data/uploads, /mnt/user-data/workspace, /mnt/user-data/outputs, "
            "or a configured bridge. Use this for visual inspection; it returns a multimodal image_url block."
        )
    )
    def view_image(path: str) -> list[dict[str, object]]:
        host_path = path_service.resolve_virtual_path(thread_id, path)
        mime_type = _image_mime_type(host_path)
        if mime_type is None:
            raise ValueError("view_image supports only PNG, JPEG, WEBP, or GIF images")
        size_bytes = host_path.stat().st_size
        if size_bytes > MAX_VIEW_IMAGE_BYTES:
            raise ValueError(f"image is too large for inline vision input: {size_bytes} bytes")
        encoded = base64.b64encode(host_path.read_bytes()).decode("ascii")
        virtual_path = path_service.to_virtual_path(thread_id, host_path)
        return [
            {
                "type": "text",
                "text": f"<view_image>\npath: {virtual_path}\nmime_type: {mime_type}\nsize_bytes: {size_bytes}",
            },
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
            {"type": "text", "text": "</view_image>"},
        ]

    @tool(
        description=(
            "Return metadata for one file or directory under /mnt/user-data/workspace, /mnt/user-data/uploads, "
            "/mnt/user-data/outputs, or a configured bridge root. Use before deciding whether to read, patch, move, or delete."
        )
    )
    def file_info(path: str) -> str:
        return json.dumps(acquire_handle().file_info(path), ensure_ascii=False)

    @tool(
        description=(
            "Extract normalized text from a document under /mnt/user-data/uploads, /mnt/user-data/workspace, "
            "/mnt/user-data/outputs, or a configured bridge root. Prefer the analysis companion when available and return provider diagnostics."
        )
    )
    def extract_document(path: str, prefer_companion: bool = True) -> str:
        host_path = path_service.resolve_virtual_path(thread_id, path)
        extracted = extract_document_file(
            host_path,
            prefer_companion=prefer_companion,
            convert_documents=uploads_config.convert_documents,
            pdf_converter=uploads_config.pdf_converter,
            ocr_enabled=uploads_config.ocr_enabled,
            ocr_strategy=uploads_config.ocr_strategy,
            ocr_languages=uploads_config.ocr_languages,
            max_ocr_pages=uploads_config.max_ocr_pages,
        )
        return json.dumps(
            {
                "path": path,
                "content_path": translate_runtime_path(path_service.to_virtual_path(thread_id, extracted.content_path)),
                "content": extracted.content,
                "provider": extracted.extraction.provider if extracted.extraction is not None else None,
                "ocr_provider": extracted.extraction.ocr_provider if extracted.extraction is not None else None,
                "diagnostics": list(extracted.diagnostics or (extracted.extraction.diagnostics if extracted.extraction else ())),
            },
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Write a UTF-8 text file to an absolute virtual runtime path under "
            "/mnt/user-data/workspace, /mnt/user-data/outputs, or a configured bridge such as "
            "/mnt/user-data/workspace/_host/<alias>. "
            "Use /mnt/user-data/workspace for working files and /mnt/user-data/outputs for deliverables. "
            "This creates a new file or replaces the whole file; use patch_file for targeted edits. "
            "Set overwrite=false when creating a file that must not already exist. "
            "Do not use /mnt/user-data itself, '.', '/', or unlisted host paths."
        )
    )
    def write_file(path: str, content: str, overwrite: bool = True) -> str:
        return json.dumps(acquire_handle().write_file(path, content, overwrite=overwrite), ensure_ascii=False)

    @tool(
        description=(
            "Apply targeted UTF-8 text edits to an existing file under /mnt/user-data/workspace or "
            "/mnt/user-data/outputs, or a configured bridge such as /mnt/user-data/workspace/_host/<alias>. "
            "Prefer patch_file when you need to insert, replace, or delete part "
            "of a file without rewriting the whole document. Supported actions: insert_before_anchor, "
            "insert_after_anchor, replace_text, delete_text, insert_before_line, insert_after_line, "
            "replace_lines, delete_lines. Set dry_run=true first when you need a unified diff before writing."
        )
    )
    def patch_file(path: str, operations: list[dict[str, object]], dry_run: bool = False) -> str:
        result = acquire_handle().patch_file(path, operations, dry_run=dry_run)
        return json.dumps(result, ensure_ascii=False)

    @tool(
        description=(
            "Delete one file or directory from /mnt/user-data/workspace, /mnt/user-data/outputs, "
            "or a configured bridge such as /mnt/user-data/workspace/_host/<alias>. "
            "Directories are only removed when empty unless recursive=true. "
            "Do not use this for broad cleanup without first listing/searching the exact target."
        )
    )
    def delete_path(path: str, recursive: bool = False) -> str:
        return json.dumps(acquire_handle().delete_path(path, recursive=recursive), ensure_ascii=False)

    @tool(
        description=(
            "Move or copy one file/directory between absolute virtual runtime paths. "
            "Set mode='copy' to duplicate instead of move, and overwrite=true only when replacing the exact destination is intended. "
            "Paths must stay under /mnt/user-data/workspace, /mnt/user-data/outputs, or configured bridge roots."
        )
    )
    def move_path(source_path: str, destination_path: str, overwrite: bool = False, mode: str = "move") -> str:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"move", "copy"}:
            raise ValueError("mode must be 'move' or 'copy'")
        return json.dumps(
            acquire_handle().move_path(source_path, destination_path, overwrite=overwrite, copy=normalized_mode == "copy"),
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Create a directory and missing parents under /mnt/user-data/workspace, /mnt/user-data/outputs, "
            "or a configured bridge such as /mnt/user-data/workspace/_host/<alias>. "
            "Use this before writing groups of related files."
        )
    )
    def make_dir(path: str) -> str:
        return json.dumps(acquire_handle().make_dir(path), ensure_ascii=False)

    @tool(
        description=(
            "Export document content to /mnt/user-data/outputs. Default mode is editable and produces a stable "
            "editable .docx. preserve_layout falls back to editable when high-fidelity providers are unavailable."
        )
    )
    def export_document(
        output_path: str,
        input_path: str | None = None,
        project_path: str | None = None,
        content: str | None = None,
        format: str = "docx",
        mode: str | None = None,
        cleanup_intermediates: bool = True,
        title: str | None = None,
        audience: str | None = None,
        style: str | None = None,
    ) -> str:
        normalized_format = format.strip().lower()
        content_ref: str | None = None
        content_source: str | None = None
        if normalized_format == "pptx":
            return json.dumps(
                {
                    "success": False,
                    "error": "PPTX generation tools have been removed from the runtime surface. Use structured user interaction to collect deck requirements, then create non-tool deliverables or external workflows explicitly approved by the user.",
                },
                ensure_ascii=False,
            )
        elif input_path:
            resolved_input_path = path_service.resolve_virtual_path(thread_id, input_path)
            extracted = extract_document_file(
                resolved_input_path,
                prefer_companion=True,
                convert_documents=uploads_config.convert_documents,
                pdf_converter=uploads_config.pdf_converter,
                ocr_enabled=uploads_config.ocr_enabled,
                ocr_strategy=uploads_config.ocr_strategy,
                ocr_languages=uploads_config.ocr_languages,
                max_ocr_pages=uploads_config.max_ocr_pages,
            )
            export_content = extracted.content
            content_source = f"input_path:{input_path}"
        elif content:
            export_content = content
            content_source = "content"
        else:
            raise ValueError("export_document requires either input_path or content")
        normalized_mode = mode or documents_config.export.default_mode
        resolved_output_path = path_service.resolve_virtual_path(thread_id, output_path)

        exported = export_document_file(
            output_path=resolved_output_path,
            content=export_content,
            format=format,
            mode=normalized_mode,
            scratch_root=path_service.thread_scratch_dir(thread_id),
            cleanup_intermediates=cleanup_intermediates and documents_config.scratch.cleanup_on_success,
        )
        return json.dumps(
            {
                "output_path": output_path,
                "format": exported.format,
                "mode": exported.mode,
                "provider": exported.provider,
                "warnings": list(exported.warnings),
                "scratch_paths": [
                    translate_runtime_path(path_service.to_virtual_path(thread_id, scratch_path))
                    for scratch_path in exported.scratch_paths
                ],
                "cleaned_scratch_paths": [
                    translate_runtime_path(path_service.to_virtual_path(thread_id, cleaned_path))
                    for cleaned_path in exported.cleaned_scratch_paths
                ],
                "preflight": exported.preflight,
                "metadata": {
                    **exported.metadata,
                    "content_source": content_source,
                    "content_ref": content_ref,
                },
            },
            ensure_ascii=False,
        )

    @tool(
        description=(
            "List direct child names for an absolute virtual runtime directory. "
            "Start discovery at /mnt/user-data, then navigate into "
            "/mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, "
            "or /mnt/user-data/workspace/_host/<alias> when bridges are configured. "
            "Set structured=true for paged entries with kind, size, mtime, and full virtual path. "
            "Do not use '.', '/', or unlisted host paths."
        )
    )
    def list_dir(path: str, structured: bool = False, offset: int = 0, limit: int = 100) -> str:
        if structured:
            return json.dumps(acquire_handle().list_dir_structured(path, offset=offset, limit=limit), ensure_ascii=False)
        normalized_path = path.strip().rstrip("/") or path.strip()
        if normalized_path in {"/mnt/user-data", "/mnt/user-data/workspace/_host"}:
            return json.dumps(acquire_handle().list_dir(path))
        listing = acquire_handle().list_dir_structured(path, offset=offset, limit=limit)
        entries = listing.get("entries", []) if isinstance(listing, dict) else []
        names = [entry["name"] for entry in entries if isinstance(entry, dict) and isinstance(entry.get("name"), str)]
        return json.dumps(names)

    @tool(
        description=(
            "Search files by name or content under absolute virtual runtime paths. "
            "Use this instead of grep, rg, find, or repeated list_dir/read_file when exploring files. "
            "Set target='files' for glob/name search and target='content' for regex or literal content search. "
            "Search roots must stay under /mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, "
            "or configured bridge virtual roots such as /mnt/user-data/workspace/_host/<alias>. Results are structured JSON with virtual paths only."
        )
    )
    def search_files(
        pattern: str | None = None,
        target: str = "content",
        path: str = "/mnt/user-data/workspace",
        file_glob: str | None = None,
        limit: int = 50,
        offset: int = 0,
        output_mode: str = "content",
        context: int = 0,
        literal: bool = False,
        case_sensitive: bool = False,
        include_hidden: bool = False,
        max_file_bytes: int = 1000000,
    ) -> str:
        effective_pattern = pattern
        effective_target = target
        if (effective_pattern is None or not str(effective_pattern).strip()) and file_glob:
            effective_pattern = file_glob
            effective_target = "files"
        payload = search_runtime_files(
            path_service=path_service,
            thread_id=thread_id,
            pattern=effective_pattern or "",
            target=effective_target,
            path=path,
            file_glob=file_glob,
            limit=limit,
            offset=offset,
            output_mode=output_mode,
            context=context,
            literal=literal,
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            max_file_bytes=max_file_bytes,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Agent-runtime Glob wrapper for fast file path discovery. "
            "Find files by glob/name pattern under /mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, "
            "or configured bridge virtual roots. This is a thin alias over search_files(target='files') and returns virtual paths only."
        )
    )
    def glob_files(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        limit: int = 50,
        offset: int = 0,
        include_hidden: bool = False,
    ) -> str:
        payload = search_runtime_files(
            path_service=path_service,
            thread_id=thread_id,
            pattern=pattern,
            target="files",
            path=path,
            limit=limit,
            offset=offset,
            include_hidden=include_hidden,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Agent-runtime Grep wrapper for bounded text search. "
            "Search file contents with regex or literal matching under governed virtual roots. "
            "Use file_glob to narrow candidate files and read_file for the returned line windows. "
            "This is a thin alias over search_files(target='content') and never shells out."
        )
    )
    def grep_files(
        pattern: str,
        path: str = "/mnt/user-data/workspace",
        file_glob: str | None = None,
        limit: int = 50,
        offset: int = 0,
        output_mode: str = "content",
        context: int = 0,
        literal: bool = False,
        case_sensitive: bool = False,
        include_hidden: bool = False,
        max_file_bytes: int = 1000000,
    ) -> str:
        payload = search_runtime_files(
            path_service=path_service,
            thread_id=thread_id,
            pattern=pattern,
            target="content",
            path=path,
            file_glob=file_glob,
            limit=limit,
            offset=offset,
            output_mode=output_mode,
            context=context,
            literal=literal,
            case_sensitive=case_sensitive,
            include_hidden=include_hidden,
            max_file_bytes=max_file_bytes,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Build a compact cached code index: files, languages, symbol counts, and internal "
            "import edges. Use code_focus for one-file blast radius, code_security_scan for security findings, "
            "code_pattern_scan for patterns, code_doc_graph for Markdown links, and code_health for summary health. "
            "Use these only for coding/refactoring/debugging work, not for general non-code tasks."
        )
    )
    def code_map(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        focus: str | None = None,
        max_files: int = 300,
        include_symbols: bool = False,
        max_edges: int = 1000,
    ) -> str:
        payload = build_code_map(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            focus=focus,
            max_files=max_files,
            include_symbols=include_symbols,
            max_edges=max_edges,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Inspect one focus file's dependencies, dependents, nearby related files, symbols, "
            "owners, and local security/pattern notes without returning the whole project graph."
        )
    )
    def code_focus(
        focus: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        depth: int = 1,
    ) -> str:
        payload = build_code_focus(
            path_service=path_service,
            thread_id=thread_id,
            focus=focus,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            depth=depth,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Return the symbol outline for one file without loading the whole code graph. "
            "Use before reading or patching a code file when symbol names and line anchors are enough."
        )
    )
    def code_symbols(
        focus: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        limit: int = 120,
    ) -> str:
        payload = build_code_symbols(
            path_service=path_service,
            thread_id=thread_id,
            focus=focus,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            limit=limit,
            code_semantics_config=code_semantics_config,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Search symbol names across the project and return bounded file/line matches. "
            "Use this instead of broad grep when looking for classes, functions, or exported names."
        )
    )
    def code_symbol_search(
        query: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        limit: int = 80,
        kind: str | None = None,
    ) -> str:
        payload = build_code_symbol_search(
            path_service=path_service,
            thread_id=thread_id,
            query=query,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            limit=limit,
            kind=kind,
            code_semantics_config=code_semantics_config,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Find bounded textual references to a symbol name, optionally inside one file. "
            "Returns line snippets with small context windows instead of entire files."
        )
    )
    def code_references(
        symbol_name: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        file_path: str | None = None,
        max_files: int = 300,
        limit: int = 100,
        context: int = 1,
    ) -> str:
        payload = build_code_references(
            path_service=path_service,
            thread_id=thread_id,
            symbol_name=symbol_name,
            path=_code_project_path(path, project_path),
            file_path=file_path,
            max_files=max_files,
            limit=limit,
            context=context,
            code_semantics_config=code_semantics_config,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Locate bounded definitions for a symbol name, optionally scoped to one file. "
            "Uses configured semantic backend when available and returns definition snippets without whole files."
        )
    )
    def code_definition(
        symbol_name: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        file_path: str | None = None,
        max_files: int = 300,
        limit: int = 20,
        context: int = 1,
    ) -> str:
        payload = build_code_definition(
            path_service=path_service,
            thread_id=thread_id,
            symbol_name=symbol_name,
            path=_code_project_path(path, project_path),
            file_path=file_path,
            max_files=max_files,
            limit=limit,
            context=context,
            code_semantics_config=code_semantics_config,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Write, validate, refresh, recover, watch, or inspect a bounded semantic index JSON and backend health. "
            "Use when preparing an external_index backend, detecting workspace changes after manual edits, recovering stale code caches, or debugging optional LSP backend status. "
            "Use mode=watch with watch_action=start/poll/status/stop when checking stale code caches after edits. "
            "Returns only summary metadata, health, and output paths, not full indexes or host paths."
        )
    )
    def code_semantic_index(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        output_path: str = "/mnt/user-data/outputs/code-semantic-index.json",
        max_files: int = 300,
        mode: str = "write",
        watch_action: str = "poll",
        auto_recover: bool | None = None,
    ) -> str:
        payload = build_code_semantic_index(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            output_path=output_path,
            max_files=max_files,
            mode=mode,
            watch_action=watch_action,
            auto_recover=auto_recover,
            code_semantics_config=code_semantics_config,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Summarize one file's language, imports, symbols, owners, markdown headings, "
            "and local risk notes without returning source content."
        )
    )
    def code_file_summary(
        file_path: str,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        include_risk_notes: bool = True,
        symbol_limit: int = 60,
    ) -> str:
        payload = build_code_file_summary(
            path_service=path_service,
            thread_id=thread_id,
            file_path=file_path,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            include_risk_notes=include_risk_notes,
            symbol_limit=symbol_limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Build a bounded change impact report for one file and optional symbol: "
            "direct dependencies, dependents, related files, symbol/reference summaries, candidate tests, docs, "
            "risk notes, and suggested next tools. Use before editing public or shared code."
        )
    )
    def code_impact(
        target_path: str,
        symbol_name: str | None = None,
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        depth: int = 1,
        limit: int = 80,
    ) -> str:
        payload = build_code_impact(
            path_service=path_service,
            thread_id=thread_id,
            target_path=target_path,
            symbol_name=symbol_name,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            depth=depth,
            limit=limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Run a bounded heuristic security scan over code files for likely secrets, dynamic "
            "code execution, SQL string construction, and debug statements. Returns findings only."
        )
    )
    def code_security_scan(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        severity: str | None = None,
        limit: int = 100,
    ) -> str:
        payload = build_code_security_scan(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            severity=severity,
            limit=limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Detect common implementation patterns and structural anti-pattern hints without "
            "returning file contents."
        )
    )
    def code_pattern_scan(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        include_anti_patterns: bool = True,
        limit: int = 100,
    ) -> str:
        payload = build_code_pattern_scan(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            include_anti_patterns=include_anti_patterns,
            limit=limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Build a bounded Markdown documentation graph with wiki/relative links, headings, "
            "and broken local links for codebase documentation work."
        )
    )
    def code_doc_graph(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
        include_headings: bool = True,
        limit: int = 300,
    ) -> str:
        payload = build_code_doc_graph(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            max_files=max_files,
            include_headings=include_headings,
            limit=limit,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Coding-task enhancer. Return a compact project health summary with hotspots, ownership coverage, "
            "security counts, pattern counts, anti-pattern counts, and documentation link health."
        )
    )
    def code_health(
        path: str = "/mnt/user-data/workspace",
        project_path: str | None = None,
        max_files: int = 300,
    ) -> str:
        payload = build_code_health(
            path_service=path_service,
            thread_id=thread_id,
            path=_code_project_path(path, project_path),
            max_files=max_files,
        )
        return json.dumps(payload, ensure_ascii=False)

    @tool(
        description=(
            "Ask the user for missing information or a structured decision. Use this when the runtime cannot safely continue without clarification."
        )
    )
    def ask_clarification(
        question: str,
        clarification_type: str = "missing_info",
        context: str | None = None,
        options: list[object] | None = None,
        fields: list[object] | None = None,
        title: str | None = None,
        response_type: str | None = None,
        selection_mode: str | None = None,
        min_selections: int | None = None,
        max_selections: int | None = None,
        allow_custom: bool = False,
        custom_label: str | None = None,
        placeholder: str | None = None,
        required: bool = True,
    ) -> str:
        payload = {
            "title": title,
            "question": question,
            "clarification_type": clarification_type,
            "context": context,
            "response_type": response_type,
            "selection_mode": selection_mode,
            "options": options or [],
            "fields": fields or [],
            "min_selections": min_selections,
            "max_selections": max_selections,
            "allow_custom": allow_custom,
            "custom_label": custom_label,
            "placeholder": placeholder,
            "required": required,
        }
        return json.dumps(payload)

    def run_command_foreground(
        command: str,
        cwd: str = "/mnt/user-data/workspace",
        timeout_seconds: int | None = None,
    ) -> str:
        translated_command = translate_command(command)
        env = build_command_env()
        backend_spec = getattr(getattr(process_service, "backend_adapter", None), "spec", None)
        configured_timeout = getattr(backend_spec, "timeout_seconds", None)
        effective_timeout = _bounded_timeout_seconds(
            timeout_seconds if timeout_seconds is not None else configured_timeout,
            default_seconds=DEFAULT_FOREGROUND_COMMAND_TIMEOUT_SECONDS,
            max_seconds=MAX_FOREGROUND_COMMAND_TIMEOUT_SECONDS,
        )
        if process_service is None:
            handle = acquire_handle()
            try:
                completed = handle.execute_command(
                    command=translated_command,
                    cwd=cwd if getattr(handle, "provider_mode", "local") == "isolated" else path_service.to_virtual_path(thread_id, path_service.resolve_virtual_path(thread_id, cwd)),
                    env=env,
                    timeout_seconds=effective_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                output = translate_runtime_output(_subprocess_output_text(exc.stdout, exc.stderr))
                return json.dumps(
                    {
                        "command_id": f"cmd-{uuid4().hex[:12]}",
                        "status": "timed_out",
                        "exit_code": None,
                        "stdout": output,
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "command": command,
                        "cwd": cwd,
                        "output": output,
                        "timed_out": True,
                        "timeout_seconds": effective_timeout,
                        "error": f"command timed out after {effective_timeout} seconds",
                    },
                    ensure_ascii=False,
                )
            result = CommandExecutionResult(
                command_id=f"cmd-{uuid4().hex[:12]}",
                status="completed" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                stdout=translate_runtime_output((completed.stdout or "") + (completed.stderr or "")),
                started_at=datetime.now(timezone.utc).isoformat(),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return json.dumps(
                {
                    **result.__dict__,
                    "command": command,
                    "cwd": cwd,
                    "output": result.stdout,
                },
                ensure_ascii=False,
            )

        session = process_service.spawn(
            thread_id=thread_id,
            command=translated_command,
            cwd=cwd if process_backend_uses_runtime_paths() else str(path_service.resolve_virtual_path(thread_id, cwd)),
            env=env,
        )
        finished = process_service.wait(session.session_id, timeout_seconds=effective_timeout)
        timed_out = getattr(finished.status, "value", finished.status) == "running"
        if timed_out and hasattr(process_service, "timeout"):
            finished = process_service.timeout(session.session_id, timeout_seconds=1)
        log_view = process_service.read_log(session.session_id)
        return json.dumps(
            {
                "session_id": finished.session_id,
                "status": finished.status.value,
                "exit_code": finished.exit_code,
                "command": command,
                "cwd": cwd,
                "output": translate_runtime_output(log_view.output),
                "timed_out": timed_out,
                "timeout_seconds": effective_timeout,
                **({"error": f"command timed out after {effective_timeout} seconds"} if timed_out else {}),
            },
            ensure_ascii=False,
        )

    @tool(
        description=(
            "Run a shell command inside the thread workspace. Set background=true to keep it running and manage it later with the process tool."
            " Use virtual paths, including configured bridges under /mnt/user-data/workspace/_host/<alias>, not raw host paths."
        )
    )
    def run_command(
        command: str,
        cwd: str = "/mnt/user-data/workspace",
        background: bool = False,
        timeout_seconds: int | None = None,
    ) -> str:
        handle = acquire_handle()
        projection = handle.projection
        host_cwd = path_service.resolve_virtual_path(thread_id, cwd)
        if not process_backend_uses_runtime_paths():
            ensure_command_cwd_allowed(host_cwd, projection)
        if background and process_service is not None:
            process_cwd = cwd if process_backend_uses_runtime_paths() else str(host_cwd)
            session = process_service.spawn(
                thread_id=thread_id,
                command=translate_command(command),
                cwd=process_cwd,
                env=build_command_env(),
            )
            return json.dumps(
                {
                    "session_id": session.session_id,
                    "status": session.status.value,
                    "command": command,
                    "cwd": cwd,
                },
                ensure_ascii=False,
            )
        return run_command_foreground(command, cwd, timeout_seconds)

    @tool(
        description=(
            "Write or update the current todo list for plan mode. "
            "Provide a JSON payload with `todos` and optional `mode`."
        )
    )
    def write_todos(payload: str) -> str:
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError(f"write_todos expects valid JSON payload: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("write_todos payload must be a JSON object")
        return json.dumps(parsed, ensure_ascii=False)

    @tool(
        description=(
            "Manage background process sessions spawned by run_command(background=true). "
            "Actions: capabilities, list, get, wait, kill, interrupt, log, write, submit, close, resize."
        )
    )
    def process(
        action: str,
        session_id: str | None = None,
        offset: int = 0,
        limit: int = 200,
        timeout_seconds: int | None = None,
        data: str = "",
        columns: int = 120,
        rows: int = 40,
    ) -> str:
        if process_service is None:
            return json.dumps({"error": "process service unavailable"})
        normalized = action.strip().lower()
        if normalized == "capabilities":
            capabilities = process_service.capabilities() if hasattr(process_service, "capabilities") else None
            return json.dumps(capabilities.model_dump(mode="json") if capabilities is not None else {"error": "capabilities unavailable"})
        if normalized == "list":
            return json.dumps(
                [
                    translate_runtime_payload(session.model_dump(mode="json"))
                    for session in process_service.list_sessions(thread_id=thread_id)
                ]
            )
        if not session_id:
            return json.dumps({"error": "session_id is required"})
        if normalized == "get":
            session = process_service.get_session(session_id)
            return json.dumps(translate_runtime_payload(session.model_dump(mode="json")) if session is not None else {"error": "not found"})
        if normalized == "wait":
            effective_timeout = _bounded_timeout_seconds(
                timeout_seconds,
                default_seconds=DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS,
                max_seconds=MAX_PROCESS_WAIT_TIMEOUT_SECONDS,
            )
            return json.dumps(
                translate_runtime_payload(
                    process_service.wait(session_id, timeout_seconds=effective_timeout).model_dump(mode="json")
                )
            )
        if normalized == "kill":
            return json.dumps(translate_runtime_payload(process_service.kill(session_id).model_dump(mode="json")))
        if normalized == "interrupt":
            return json.dumps(translate_runtime_payload(process_service.interrupt(session_id).model_dump(mode="json")))
        if normalized == "log":
            cursor = offset if offset > 0 else None
            return json.dumps(translate_runtime_payload(process_service.read_log(session_id, offset=offset, cursor=cursor, limit=limit).model_dump(mode="json")))
        if normalized == "write":
            return json.dumps(translate_runtime_payload(process_service.write_stdin(session_id, data, submit=False).model_dump(mode="json")))
        if normalized == "submit":
            return json.dumps(translate_runtime_payload(process_service.write_stdin(session_id, data, submit=True).model_dump(mode="json")))
        if normalized == "close":
            return json.dumps(translate_runtime_payload(process_service.close_stdin(session_id).model_dump(mode="json")))
        if normalized == "resize":
            return json.dumps(translate_runtime_payload(process_service.resize(session_id, columns=columns, rows=rows).model_dump(mode="json")))
        return json.dumps({"error": f"unsupported action: {action}"})

    @tool(
        description=(
            "Manage scheduled automations. Actions: list, history, create, update, pause, resume, run, remove. "
            "Use this only when the user asks to schedule, automate, remind, or inspect scheduled work. "
            "Do not recursively create scheduled tasks from within a scheduled automation run."
        )
    )
    def scheduled_task(
        action: str,
        task_id: str | None = None,
        name: str | None = None,
        prompt: str | None = None,
        schedule: str | None = None,
        enabled: bool | None = None,
        execution_mode: str = "agent",
        selected_model: str | None = None,
        selected_profile: str | None = None,
        selected_reasoning_effort: str | None = None,
        promoted_capabilities: list[str] | None = None,
        max_runs: int | None = None,
        force: bool = True,
        limit: int = 20,
    ) -> str:
        if scheduled_task_service is None:
            return json.dumps({"success": False, "error": "scheduled task service unavailable"})
        normalized = action.strip().lower()
        try:
            if normalized == "list":
                return json.dumps(
                    {
                        "success": True,
                        "tasks": [
                            task.model_dump(mode="json")
                            for task in scheduled_task_service.list_tasks(include_disabled=True)
                        ],
                    },
                    ensure_ascii=False,
                )
            if normalized == "history":
                return json.dumps(
                    {
                        "success": True,
                        "executions": [
                            execution.model_dump(mode="json")
                            for execution in scheduled_task_service.list_executions(task_id=task_id, limit=limit)
                        ],
                    },
                    ensure_ascii=False,
                )
            if normalized == "create":
                if not name:
                    return json.dumps({"success": False, "error": "name is required"})
                if not prompt:
                    return json.dumps({"success": False, "error": "prompt is required"})
                if not schedule:
                    return json.dumps({"success": False, "error": "schedule is required"})
                task = scheduled_task_service.create_task(
                    ScheduledTaskCreateRequest(
                        task_id=task_id,
                        name=name,
                        prompt=prompt,
                        schedule=schedule,
                        enabled=True if enabled is None else enabled,
                        thread_id=thread_id,
                        execution_mode=execution_mode,
                        selected_model=selected_model,
                        selected_profile=selected_profile,
                        selected_reasoning_effort=selected_reasoning_effort,
                        promoted_capabilities=tuple(promoted_capabilities or ()),
                        max_runs=max_runs,
                    )
                )
                return json.dumps({"success": True, "task": task.model_dump(mode="json")}, ensure_ascii=False)
            if not task_id:
                return json.dumps({"success": False, "error": "task_id is required"})
            if normalized == "update":
                updates = {}
                if name is not None:
                    updates["name"] = name
                if prompt is not None:
                    updates["prompt"] = prompt
                if schedule is not None:
                    updates["schedule"] = schedule
                if enabled is not None:
                    updates["enabled"] = enabled
                if execution_mode is not None:
                    updates["execution_mode"] = execution_mode
                if selected_model is not None:
                    updates["selected_model"] = selected_model
                if selected_profile is not None:
                    updates["selected_profile"] = selected_profile
                if selected_reasoning_effort is not None:
                    updates["selected_reasoning_effort"] = selected_reasoning_effort
                if promoted_capabilities is not None:
                    updates["promoted_capabilities"] = tuple(promoted_capabilities)
                if max_runs is not None:
                    updates["max_runs"] = max_runs
                task = scheduled_task_service.update_task(
                    task_id,
                    ScheduledTaskUpdateRequest(**updates),
                )
                return json.dumps({"success": True, "task": task.model_dump(mode="json")}, ensure_ascii=False)
            if normalized == "pause":
                return json.dumps({"success": True, "task": scheduled_task_service.pause_task(task_id).model_dump(mode="json")}, ensure_ascii=False)
            if normalized == "resume":
                return json.dumps({"success": True, "task": scheduled_task_service.resume_task(task_id).model_dump(mode="json")}, ensure_ascii=False)
            if normalized == "run":
                result = scheduled_task_service.run_task(task_id, force=force)
                return json.dumps({"success": True, **result.model_dump(mode="json")}, ensure_ascii=False)
            if normalized == "remove":
                return json.dumps({"success": True, "task": scheduled_task_service.remove_task(task_id).model_dump(mode="json")}, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)
        return json.dumps({"success": False, "error": f"unsupported action: {action}"})

    memory_tools = build_memory_tools(memory_manager=memory_manager, thread_id=thread_id)

    tool_metadata = {
        "read_file": {
            "summary": "Read one UTF-8 text/document file, optionally as a bounded line window with metadata.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer", "minimum": 1},
                    "max_lines": {"type": ["integer", "null"], "minimum": 1},
                    "max_chars": {"type": ["integer", "null"], "minimum": 1},
                    "structured": {"type": "boolean"},
                    "numbered": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
        "view_image": {
            "summary": "Inspect one local image as a multimodal image_url block for vision-capable models.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
            "output_budget": 64000,
        },
        "file_info": {
            "summary": "Inspect one file or directory's kind, size, line count, and modified timestamp before acting.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        "extract_document": {
            "summary": "Extract normalized text and diagnostics from one uploaded or workspace document.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "prefer_companion": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
        "write_file": {
            "summary": "Create a new file or intentionally replace the entire contents of one file.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
        },
        "patch_file": {
            "summary": "Apply targeted in-place text edits to an existing file using anchors or line ranges.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "operations": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": [
                                        "insert_before_anchor",
                                        "insert_after_anchor",
                                        "replace_text",
                                        "delete_text",
                                        "insert_before_line",
                                        "insert_after_line",
                                        "replace_lines",
                                        "delete_lines",
                                    ],
                                },
                                "anchor": {"type": "string"},
                                "text": {"type": "string"},
                                "content": {"type": "string"},
                                "line": {"type": "integer", "minimum": 1},
                                "start_line": {"type": "integer", "minimum": 1},
                                "end_line": {"type": "integer", "minimum": 1},
                                "expected_old_text": {"type": "string"},
                            },
                            "required": ["action"],
                        },
                    },
                    "dry_run": {"type": "boolean"},
                },
                "required": ["path", "operations"],
            },
        },
        "export_document": {
            "summary": "Write one editable DOCX deliverable into outputs without shelling out manually. PPTX generation is no longer exposed as a runtime tool.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "output_path": {"type": "string"},
                    "input_path": {"type": ["string", "null"]},
                    "project_path": {"type": ["string", "null"]},
                    "content": {"type": ["string", "null"]},
                    "format": {"type": "string", "enum": ["docx"]},
                    "mode": {"type": ["string", "null"]},
                    "cleanup_intermediates": {"type": "boolean"},
                },
                "required": ["output_path"],
            },
        },
        "delete_path": {
            "summary": "Delete one exact file or empty directory; recursive directory deletion is explicit.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "recursive": {"type": "boolean"},
                },
                "required": ["path"],
            },
        },
        "move_path": {
            "summary": "Move or copy one exact file/directory between virtual runtime paths.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "source_path": {"type": "string"},
                    "destination_path": {"type": "string"},
                    "overwrite": {"type": "boolean"},
                    "mode": {"type": "string", "enum": ["move", "copy"]},
                },
                "required": ["source_path", "destination_path"],
            },
        },
        "make_dir": {
            "summary": "Create one directory and missing parents inside the virtual runtime roots.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                },
                "required": ["path"],
            },
        },
        "list_dir": {
            "summary": "Discover direct children inside virtual runtime roots; structured mode returns paged entry metadata.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "structured": {"type": "boolean"},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["path"],
            },
        },
        "search_files": {
            "summary": "Find candidate files or content matches through a read-only, bounded virtual-path search instead of shell grep/find.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "target": {"type": "string", "enum": ["content", "files", "grep", "glob", "find"]},
                    "path": {"type": "string"},
                    "file_glob": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "offset": {"type": "integer", "minimum": 0},
                    "output_mode": {"type": "string", "enum": ["content", "files_only", "count"]},
                    "context": {"type": "integer", "minimum": 0, "maximum": 5},
                    "literal": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                    "include_hidden": {"type": "boolean"},
                    "max_file_bytes": {"type": "integer", "minimum": 1, "maximum": 10000000},
                },
            },
        },
        "glob_files": {
            "summary": "Find files by glob/name pattern through a read-only, bounded virtual-path search.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "offset": {"type": "integer", "minimum": 0},
                    "include_hidden": {"type": "boolean"},
                },
                "required": ["pattern"],
            },
        },
        "grep_files": {
            "summary": "Search file contents with regex or literal matching through a read-only, bounded virtual-path search.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "file_glob": {"type": ["string", "null"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "offset": {"type": "integer", "minimum": 0},
                    "output_mode": {"type": "string", "enum": ["content", "files_only", "count"]},
                    "context": {"type": "integer", "minimum": 0, "maximum": 5},
                    "literal": {"type": "boolean"},
                    "case_sensitive": {"type": "boolean"},
                    "include_hidden": {"type": "boolean"},
                    "max_file_bytes": {"type": "integer", "minimum": 1, "maximum": 10000000},
                },
                "required": ["pattern"],
            },
        },
        "code_map": {
            "summary": "Build a compact cached code index for the selected project path with files, languages, symbol counts, and internal import edges.",
            "output_budget": 12000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "focus": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "include_symbols": {"type": "boolean"},
                    "max_edges": {"type": "integer", "minimum": 1, "maximum": 10000},
                },
            },
        },
        "code_focus": {
            "summary": "Inspect one focus file's dependencies, dependents, nearby related files, symbols, owners, and local coding risk notes.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["focus"],
            },
        },
        "code_symbols": {
            "summary": "Return a bounded symbol outline for one code file without returning the full code graph.",
            "output_budget": 8000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "focus": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["focus"],
            },
        },
        "code_symbol_search": {
            "summary": "Search symbol names across the project and return bounded file/line matches.",
            "output_budget": 9000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "kind": {"type": ["string", "null"]},
                },
                "required": ["query"],
            },
        },
        "code_references": {
            "summary": "Find bounded textual references to a symbol name with small context windows.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "file_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "context": {"type": "integer", "minimum": 0, "maximum": 5},
                },
                "required": ["symbol_name"],
            },
        },
        "code_definition": {
            "summary": "Locate bounded definitions for a symbol name with snippets and small context windows.",
            "output_budget": 8000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol_name": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "file_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    "context": {"type": "integer", "minimum": 0, "maximum": 5},
                },
                "required": ["symbol_name"],
            },
        },
        "code_semantic_index": {
            "summary": "Write, validate, refresh, recover, watch, or inspect semantic index JSON and optional semantic backend health.",
            "output_budget": 4000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "output_path": {"type": "string"},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "mode": {"type": "string", "enum": ["write", "validate", "refresh", "recover", "health", "watch"]},
                    "watch_action": {"type": "string", "enum": ["start", "poll", "stop", "status"]},
                    "auto_recover": {"type": ["boolean", "null"]},
                },
            },
        },
        "code_file_summary": {
            "summary": "Summarize one file's imports, symbols, owners, markdown headings, and local risk notes without source content.",
            "output_budget": 9000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "include_risk_notes": {"type": "boolean"},
                    "symbol_limit": {"type": "integer", "minimum": 1, "maximum": 300},
                },
                "required": ["file_path"],
            },
        },
        "code_impact": {
            "summary": "Build a bounded change impact report for one file and optional symbol before editing shared code.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "target_path": {"type": "string"},
                    "symbol_name": {"type": ["string", "null"]},
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "depth": {"type": "integer", "minimum": 1, "maximum": 3},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
                "required": ["target_path"],
            },
        },
        "code_security_scan": {
            "summary": "Return bounded heuristic security findings for likely secrets, dynamic execution, SQL string construction, and debug statements.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "severity": {"type": ["string", "null"], "enum": ["critical", "high", "medium", "low", None]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        },
        "code_pattern_scan": {
            "summary": "Return implementation pattern hits and structural anti-pattern hints without dumping project files.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "include_anti_patterns": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                },
            },
        },
        "code_doc_graph": {
            "summary": "Return a bounded Markdown documentation graph with headings, wiki/relative links, and broken local links.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "include_headings": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
            },
        },
        "code_health": {
            "summary": "Return compact project health: hotspots, ownership coverage, security counts, pattern counts, and doc link health.",
            "output_budget": 10000,
            "input_schema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "project_path": {"type": ["string", "null"]},
                    "max_files": {"type": "integer", "minimum": 1, "maximum": 2000},
                },
            },
        },
        "ask_clarification": {
            "summary": "Ask the user for missing information or a structured decision when the runtime cannot continue safely. Use fields for one bundled decision form with multiple related questions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": ["string", "null"]},
                    "question": {"type": "string"},
                    "clarification_type": {"type": "string"},
                    "context": {"type": ["string", "null"]},
                    "response_type": {
                        "type": ["string", "null"],
                        "enum": ["single_select", "multi_select", "free_text", "single", "multiple", "text", None],
                    },
                    "selection_mode": {"type": ["string", "null"], "enum": ["single", "multiple", "text", None]},
                    "options": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {"type": "string"},
                                {
                                    "type": "object",
                                    "properties": {
                                        "id": {"type": "string"},
                                        "label": {"type": "string"},
                                        "description": {"type": ["string", "null"]},
                                        "recommended": {"type": "boolean"},
                                        "disabled": {"type": "boolean"},
                                        "metadata": {"type": "object"},
                                    },
                                    "required": ["label"],
                                },
                            ]
                        },
                    },
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "field_id": {"type": "string"},
                                "id": {"type": "string"},
                                "name": {"type": "string"},
                                "label": {"type": "string"},
                                "question": {"type": "string"},
                                "title": {"type": "string"},
                                "description": {"type": ["string", "null"]},
                                "selection_mode": {"type": ["string", "null"], "enum": ["single", "multiple", "text", None]},
                                "response_type": {
                                    "type": ["string", "null"],
                                    "enum": ["single_select", "multi_select", "free_text", "single", "multiple", "text", None],
                                },
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "oneOf": [
                                            {"type": "string"},
                                            {
                                                "type": "object",
                                                "properties": {
                                                    "id": {"type": "string"},
                                                    "label": {"type": "string"},
                                                    "description": {"type": ["string", "null"]},
                                                    "recommended": {"type": "boolean"},
                                                    "disabled": {"type": "boolean"},
                                                    "metadata": {"type": "object"},
                                                },
                                                "required": ["label"],
                                            },
                                        ]
                                    },
                                },
                                "min_selections": {"type": ["integer", "null"], "minimum": 0},
                                "max_selections": {"type": ["integer", "null"], "minimum": 1},
                                "allow_custom": {"type": "boolean"},
                                "custom_label": {"type": ["string", "null"]},
                                "placeholder": {"type": ["string", "null"]},
                                "required": {"type": "boolean"},
                                "metadata": {"type": "object"},
                            },
                        },
                    },
                    "min_selections": {"type": ["integer", "null"], "minimum": 0},
                    "max_selections": {"type": ["integer", "null"], "minimum": 1},
                    "allow_custom": {"type": "boolean"},
                    "custom_label": {"type": ["string", "null"]},
                    "placeholder": {"type": ["string", "null"]},
                    "required": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        "write_todos": {
            "summary": "Update the current plan-mode todo list with structured JSON.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "payload": {"type": "string"},
                },
                "required": ["payload"],
            },
        },
        "run_command": {
            "summary": "Run a shell command only when file tools or higher-level tools cannot do the job directly.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                    "background": {"type": "boolean"},
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "maximum": MAX_FOREGROUND_COMMAND_TIMEOUT_SECONDS,
                    },
                },
                "required": ["command"],
            },
        },
        "process": {
            "summary": "Inspect, wait for, log, or terminate background process sessions.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["capabilities", "list", "get", "wait", "kill", "interrupt", "log", "write", "submit", "close", "resize"],
                    },
                    "session_id": {"type": ["string", "null"]},
                    "offset": {"type": "integer", "minimum": 0},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
                    "timeout_seconds": {
                        "type": ["integer", "null"],
                        "minimum": 0,
                        "maximum": MAX_PROCESS_WAIT_TIMEOUT_SECONDS,
                    },
                    "data": {"type": "string"},
                    "columns": {"type": "integer", "minimum": 1, "maximum": 500},
                    "rows": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["action"],
            },
        },
        "scheduled_task": {
            "summary": "Create, inspect, run, pause, resume, or remove scheduled automations.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "history", "create", "update", "pause", "resume", "run", "remove"],
                    },
                    "task_id": {"type": ["string", "null"]},
                    "name": {"type": ["string", "null"]},
                    "prompt": {"type": ["string", "null"]},
                    "schedule": {"type": ["string", "null"]},
                    "enabled": {"type": ["boolean", "null"]},
                    "execution_mode": {"type": "string"},
                    "selected_model": {"type": ["string", "null"]},
                    "selected_profile": {"type": ["string", "null"]},
                    "selected_reasoning_effort": {"type": ["string", "null"]},
                    "promoted_capabilities": {
                        "type": ["array", "null"],
                        "items": {"type": "string"},
                    },
                    "max_runs": {"type": ["integer", "null"], "minimum": 1},
                    "force": {"type": "boolean"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
                "required": ["action"],
            },
        },
        "memory": {
            "summary": "Inspect or update durable memory layers without leaving the governed memory surface.",
        },
        "session_search": {
            "summary": "Search prior thread archives and recall evidence before redoing known work.",
        },
        "memory_trace": {
            "summary": "Inspect why memory or recall items were surfaced for the current thread.",
        },
    }

    runtime_tool_specs = [
        (read_file, "read_file", "Read File"),
        (file_info, "file_info", "File Info"),
        (extract_document, "extract_document", "Extract Document"),
        (write_file, "write_file", "Write File"),
        (patch_file, "patch_file", "Patch File"),
        (export_document, "export_document", "Export Document"),
        (delete_path, "delete_path", "Delete Path"),
        (move_path, "move_path", "Move Path"),
        (make_dir, "make_dir", "Make Directory"),
        (list_dir, "list_dir", "List Directory"),
        (search_files, "search_files", "Search Files"),
        (glob_files, "glob_files", "Glob Files"),
        (grep_files, "grep_files", "Grep Files"),
        (code_map, "code_map", "Code Map"),
        (code_focus, "code_focus", "Code Focus"),
        (code_symbols, "code_symbols", "Code Symbols"),
        (code_symbol_search, "code_symbol_search", "Code Symbol Search"),
        (code_references, "code_references", "Code References"),
        (code_definition, "code_definition", "Code Definition"),
        (code_semantic_index, "code_semantic_index", "Code Semantic Index"),
        (code_file_summary, "code_file_summary", "Code File Summary"),
        (code_impact, "code_impact", "Code Impact"),
        (code_security_scan, "code_security_scan", "Code Security Scan"),
        (code_pattern_scan, "code_pattern_scan", "Code Pattern Scan"),
        (code_doc_graph, "code_doc_graph", "Code Documentation Graph"),
        (code_health, "code_health", "Code Health"),
        *memory_tools,
        (ask_clarification, "ask_clarification", "Ask Clarification"),
        (write_todos, "write_todos", "Write Todos"),
        (run_command, "run_command", "Run Command"),
        (process, "process", "Process Registry"),
        (scheduled_task, "scheduled_task", "Scheduled Automations"),
    ]
    if vision_enabled:
        runtime_tool_specs.insert(1, (view_image, "view_image", "View Image"))

    for tool_obj, name, display_name in runtime_tool_specs:
        metadata = tool_metadata.get(name, {})
        input_schema = _runtime_tool_input_schema(tool_obj, metadata)
        registry.register(
            ToolRegistryEntry(
                name=name,
                display_name=display_name,
                source_kind=ToolSourceKind.BUILTIN,
                source_id="core",
                capability_group="coding" if name in CODING_TOOL_NAMES else "filesystem" if name in filesystem_tool_names else "memory" if name in {"memory", "session_search", "memory_trace"} else "planning" if name == "write_todos" else "control_flow" if name == "ask_clarification" else "execution" if name == "run_command" else "process" if name == "process" else "automation",
                summary=metadata.get("summary"),
                handler=_runtime_structured_tool_handler(tool_obj=tool_obj, name=name, input_schema=input_schema),
                input_schema=input_schema,
                approval_profile="filesystem_write" if name in {"write_file", "patch_file", "delete_path", "move_path", "make_dir", "export_document", "code_semantic_index"} else "shell_command" if name == "run_command" else None,
                risk_category="filesystem_write" if name in {"write_file", "patch_file", "move_path", "make_dir", "export_document", "code_semantic_index"} else "filesystem_delete" if name == "delete_path" else "coding_analysis" if name in CODING_TOOL_NAMES else "memory_read" if name in {"memory", "session_search", "memory_trace"} else "planning" if name == "write_todos" else "control_flow" if name == "ask_clarification" else "shell_execution" if name == "run_command" else "process" if name == "process" else "automation_write" if name == "scheduled_task" else "filesystem",
                output_budget=metadata.get("output_budget"),
            )
        )

    bundle = registry.build_bundle(
        effective_config_fingerprint=effective_config_fingerprint,
        enabled_source_ids={"core"},
        allowed_capability_groups={
            "filesystem",
            "coding",
            "memory",
            "planning",
            "control_flow",
            "execution",
            "process",
            "automation",
        },
    )
    return registry, bundle
