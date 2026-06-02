from __future__ import annotations

import json
import hashlib
import sys
import textwrap
import time
import zipfile
from pathlib import Path

from anvil.config import CodeSemanticsConfig
from anvil.documents import ExportedDocumentResult
from anvil.processes import ProcessSessionStatus, TerminalBackendCapabilities, TerminalBackendKind, TerminalBackendSpec
from anvil.processes.service import ProcessService
from anvil.sandbox import PathService
from anvil.sandbox.local_provider import LocalSandboxProvider
from anvil.tools import assembly as assembly_module
from anvil.tools import code_map as code_map_module
from anvil.tools import code_semantics as code_semantics_module
from anvil.tools import file_search as file_search_module
from anvil.scheduled_tasks import ScheduledTaskService, ScheduledTaskStore
from anvil.tools.assembly import assemble_runtime_tools
from anvil.tools.code_semantics import close_lsp_session_pool


STATIC_SCHEMA_RUNTIME_TOOL_NAMES = {
    "read_file",
    "view_image",
    "file_info",
    "extract_document",
    "write_file",
    "patch_file",
    "export_document",
    "delete_path",
    "move_path",
    "make_dir",
    "list_dir",
    "search_files",
    "glob_files",
    "grep_files",
    "code_map",
    "code_focus",
    "code_symbols",
    "code_symbol_search",
    "code_references",
    "code_definition",
    "code_semantic_index",
    "code_file_summary",
    "code_impact",
    "code_security_scan",
    "code_pattern_scan",
    "code_doc_graph",
    "code_health",
    "ask_clarification",
    "memory",
    "session_search",
    "memory_trace",
    "write_todos",
    "run_command",
    "process",
    "scheduled_task",
}


class RecordingProcessService:
    def __init__(self, capabilities: TerminalBackendCapabilities) -> None:
        self._capabilities = capabilities
        self.backend_adapter = type("Adapter", (), {"spec": TerminalBackendSpec(timeout_seconds=7)})()
        self.spawn_calls: list[dict[str, object]] = []

    def capabilities(self) -> TerminalBackendCapabilities:
        return self._capabilities

    def spawn(self, **kwargs):
        self.spawn_calls.append(kwargs)
        raise RuntimeError("stop after recording spawn arguments")


class _ProcessToolSession:
    def __init__(self, *, session_id: str = "proc_test", status: ProcessSessionStatus = ProcessSessionStatus.RUNNING) -> None:
        self.session_id = session_id
        self.status = status
        self.exit_code = None

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "thread_id": "thread-process",
            "command": "sleep",
            "cwd": "/mnt/user-data/workspace",
            "backend": "local",
            "backend_id": "local",
            "backend_label": "Local shell",
            "interactive": True,
            "pty": False,
            "pid": 123,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "detached": False,
            "log_cursor": 0,
            "stdin_closed": False,
            "last_stdin_at": None,
            "last_signal": None,
            "last_signal_at": None,
            "columns": None,
            "rows": None,
            "input_history": [],
            "started_at": "2026-05-30T00:00:00Z",
            "completed_at": "2026-05-30T00:00:01Z" if self.status is not ProcessSessionStatus.RUNNING else None,
            "log_path": "/mnt/user-data/workspace/process.log",
            "last_output": "still running",
        }


class _ProcessToolLog:
    output = "still running"


class TimeoutRecordingProcessService:
    def __init__(self, *, backend_timeout_seconds: int | None = 999) -> None:
        self.backend_adapter = type("Adapter", (), {"spec": TerminalBackendSpec(timeout_seconds=backend_timeout_seconds)})()
        self.spawn_calls: list[dict[str, object]] = []
        self.wait_calls: list[dict[str, object]] = []
        self.timeout_calls: list[str] = []
        self.session = _ProcessToolSession()

    def capabilities(self) -> TerminalBackendCapabilities:
        return TerminalBackendCapabilities()

    def spawn(self, **kwargs):
        self.spawn_calls.append(kwargs)
        return self.session

    def wait(self, session_id: str, *, timeout_seconds: int | None = None):
        self.wait_calls.append({"session_id": session_id, "timeout_seconds": timeout_seconds})
        return self.session

    def timeout(self, session_id: str, *, timeout_seconds: int | None = None):
        self.timeout_calls.append(session_id)
        self.session = _ProcessToolSession(session_id=session_id, status=ProcessSessionStatus.TIMED_OUT)
        self.session.exit_code = -15
        return self.session

    def read_log(self, session_id: str):
        return _ProcessToolLog()


def test_tool_assembly_materializes_current_v2_builtin_runtime_surface(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    registry, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )

    assert sorted(entry.name for entry in bundle.visible_tools) == [
        "ask_clarification",
        "code_definition",
        "code_doc_graph",
        "code_file_summary",
        "code_focus",
        "code_health",
        "code_impact",
        "code_map",
        "code_pattern_scan",
        "code_references",
        "code_security_scan",
        "code_semantic_index",
        "code_symbol_search",
        "code_symbols",
        "delete_path",
        "export_document",
        "extract_document",
        "file_info",
        "glob_files",
        "grep_files",
        "list_dir",
        "make_dir",
        "memory",
        "memory_trace",
        "move_path",
        "patch_file",
        "process",
        "read_file",
        "run_command",
        "scheduled_task",
        "search_files",
        "session_search",
        "view_image",
        "write_file",
        "write_todos",
    ]
    assert bundle.deferred_tools == ()
    assert all(not entry.name.startswith("presentation_") for entry in bundle.visible_tools)
    assert len(registry.entries()) == 35


def test_tool_handlers_execute_against_local_sandbox(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}
    write_result = json.loads(handlers["write_file"].invoke({"path": "/mnt/user-data/workspace/example.txt", "content": "hello"}))
    patch_result = json.loads(
        handlers["patch_file"].invoke(
            {
                "path": "/mnt/user-data/workspace/example.txt",
                "operations": [
                    {
                        "action": "replace_text",
                        "text": "hello",
                        "content": "hello world",
                        "expected_old_text": "hello",
                    }
                ],
            }
        )
    )
    read_result = handlers["read_file"].invoke({"path": "/mnt/user-data/workspace/example.txt"})
    list_result = handlers["list_dir"].invoke({"path": "/mnt/user-data/workspace"})

    assert write_result == {
        "path": "/mnt/user-data/workspace/example.txt",
        "operation": "created",
        "bytes_written": 5,
        "line_count": 1,
    }
    assert read_result == "hello world"
    assert patch_result["operations_applied"] == 1
    assert "example.txt" in json.loads(list_result)


def test_view_image_returns_multimodal_tool_content(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    thread_id = "thread-view-image"
    image_path = path_service.thread_uploads_dir(thread_id) / "diagram.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nsample-image")
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id=thread_id,
        effective_config_fingerprint="cfg-1",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}
    result = handlers["view_image"].invoke({"path": "/mnt/user-data/uploads/diagram.png"})

    assert isinstance(result, list)
    assert result[0]["type"] == "text"
    assert "/mnt/user-data/uploads/diagram.png" in result[0]["text"]
    assert result[1]["type"] == "image_url"
    assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_export_document_no_longer_exposes_pptx_generation(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    result = handlers["export_document"].invoke(
        {
            "output_path": "/mnt/user-data/outputs/deck.pptx",
            "content": "# Deck",
            "format": "pptx",
        }
    )

    payload = json.loads(result)
    assert payload["success"] is False
    assert "PPTX generation tools have been removed" in payload["error"]


def test_file_tools_support_bounded_reads_and_crud_operations(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-files",
        effective_config_fingerprint="cfg-files",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    make_result = json.loads(handlers["make_dir"].invoke({"path": "/mnt/user-data/workspace/src"}))
    write_result = json.loads(
        handlers["write_file"].invoke(
            {
                "path": "/mnt/user-data/workspace/src/notes.txt",
                "content": "one\ntwo\nthree\nfour\n",
                "overwrite": False,
            }
        )
    )
    read_window = json.loads(
        handlers["read_file"].invoke(
            {
                "path": "/mnt/user-data/workspace/src/notes.txt",
                "start_line": 2,
                "max_lines": 2,
                "structured": True,
                "numbered": True,
            }
        )
    )
    file_info = json.loads(handlers["file_info"].invoke({"path": "/mnt/user-data/workspace/src/notes.txt"}))
    list_structured = json.loads(
        handlers["list_dir"].invoke(
            {
                "path": "/mnt/user-data/workspace/src",
                "structured": True,
                "limit": 1,
            }
        )
    )
    dry_run = json.loads(
        handlers["patch_file"].invoke(
            {
                "path": "/mnt/user-data/workspace/src/notes.txt",
                "dry_run": True,
                "operations": [
                    {
                        "action": "replace_lines",
                        "start_line": 2,
                        "end_line": 2,
                        "content": "TWO\n",
                    }
                ],
            }
        )
    )
    assert dry_run["dry_run"] is True
    assert "-two" in dry_run["diff"]
    assert "+TWO" in dry_run["diff"]
    source_after_dry_run = (path_service.thread_workspace_dir("thread-files") / "src" / "notes.txt").read_text(
        encoding="utf-8"
    )
    copy_result = json.loads(
        handlers["move_path"].invoke(
            {
                "source_path": "/mnt/user-data/workspace/src/notes.txt",
                "destination_path": "/mnt/user-data/workspace/src/notes-copy.txt",
                "mode": "copy",
            }
        )
    )
    move_result = json.loads(
        handlers["move_path"].invoke(
            {
                "source_path": "/mnt/user-data/workspace/src/notes-copy.txt",
                "destination_path": "/mnt/user-data/workspace/notes-final.txt",
            }
        )
    )
    delete_result = json.loads(handlers["delete_path"].invoke({"path": "/mnt/user-data/workspace/src/notes.txt"}))

    assert make_result == {"path": "/mnt/user-data/workspace/src", "existed": False}
    assert write_result["operation"] == "created"
    assert read_window == {
        "path": "/mnt/user-data/workspace/src/notes.txt",
        "content": "2: two\n3: three\n",
        "start_line": 2,
        "end_line": 3,
        "total_lines": 4,
        "total_bytes": 19,
        "truncated": True,
    }
    assert file_info["kind"] == "file"
    assert file_info["line_count"] == 4
    assert list_structured["total_count"] == 1
    assert list_structured["entries"][0]["name"] == "notes.txt"
    assert list_structured["entries"][0]["path"] == "/mnt/user-data/workspace/src/notes.txt"
    assert list_structured["entries"][0]["kind"] == "file"
    assert list_structured["entries"][0]["size_bytes"] >= 19
    assert list_structured["entries"][0]["modified_at"]
    assert source_after_dry_run.replace("\r\n", "\n") == "one\ntwo\nthree\nfour\n"
    assert copy_result["operation"] == "copied"
    assert copy_result["source_kind"] == "file"
    assert move_result["operation"] == "moved"
    assert move_result["destination_path"] == "/mnt/user-data/workspace/notes-final.txt"
    assert delete_result == {"path": "/mnt/user-data/workspace/src/notes.txt", "kind": "file", "recursive": False}
    assert not (path_service.thread_workspace_dir("thread-files") / "src" / "notes.txt").exists()
    assert (path_service.thread_workspace_dir("thread-files") / "notes-final.txt").read_text(encoding="utf-8") == "one\ntwo\nthree\nfour\n"


def test_list_dir_structured_stops_at_runtime_entry_budget(contract_tmp_path, monkeypatch) -> None:
    from anvil.sandbox import file_ops as file_ops_module

    monkeypatch.setattr(file_ops_module, "DEFAULT_MAX_DIRECTORY_ENTRIES", 4)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-list-budget",
        effective_config_fingerprint="cfg-list-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-list-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (workspace / f"entry_{index:02d}.txt").write_text("entry\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["list_dir"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "structured": True,
                "limit": 2,
            }
        )
    )

    assert payload["returned_count"] == 2
    assert payload["truncated"] is True
    assert payload["scan_truncated"] is True
    assert payload["scanned_count"] == 4
    assert payload["max_entries"] == 4


def test_list_dir_plain_uses_same_runtime_entry_budget(contract_tmp_path, monkeypatch) -> None:
    from anvil.sandbox import file_ops as file_ops_module

    monkeypatch.setattr(file_ops_module, "DEFAULT_MAX_DIRECTORY_ENTRIES", 3)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-list-plain-budget",
        effective_config_fingerprint="cfg-list-plain-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-list-plain-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(9):
        (workspace / f"plain_{index:02d}.txt").write_text("entry\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["list_dir"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "limit": 2,
            }
        )
    )

    assert payload == ["plain_00.txt", "plain_01.txt"]


def test_search_files_finds_paths_and_content_without_shelling_out(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )
    workspace = path_service.thread_workspace_dir("thread-1")
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "src" / "notes.md").write_text("calculator docs\n", encoding="utf-8")
    (workspace / "node_modules" / "ignored").mkdir(parents=True)
    (workspace / "node_modules" / "ignored" / "calculator.py").write_text("ignored\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    file_payload = json.loads(
        handlers["search_files"].invoke(
            {
                "pattern": "**/*.py",
                "target": "files",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    content_payload = json.loads(
        handlers["search_files"].invoke(
            {
                "pattern": "return a \\+ b",
                "target": "content",
                "path": "/mnt/user-data/workspace",
                "file_glob": "**/*.py",
                "limit": 5,
            }
        )
    )
    literal_payload = json.loads(
        handlers["search_files"].invoke(
            {
                "pattern": "CALCULATOR DOCS",
                "target": "content",
                "path": "/mnt/user-data/workspace",
                "literal": True,
            }
        )
    )

    assert file_payload["files"] == ["/mnt/user-data/workspace/src/calculator.py"]
    assert content_payload["matches"] == [
        {
            "path": "/mnt/user-data/workspace/src/calculator.py",
            "line": 2,
            "text": "    return a + b",
        }
    ]
    assert literal_payload["matches"][0]["path"] == "/mnt/user-data/workspace/src/notes.md"
    assert file_payload["stats"]["ignored_dirs"] >= 1


def test_glob_and_grep_file_aliases_reuse_bounded_virtual_search(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-file-aliases",
        effective_config_fingerprint="cfg-file-aliases",
    )
    workspace = path_service.thread_workspace_dir("thread-file-aliases")
    (workspace / "src").mkdir(parents=True)
    (workspace / "src" / "calculator.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "src" / "notes.md").write_text("calculator docs\n", encoding="utf-8")
    (workspace / "node_modules" / "ignored").mkdir(parents=True)
    (workspace / "node_modules" / "ignored" / "calculator.py").write_text("ignored\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    glob_payload = json.loads(
        handlers["glob_files"].invoke(
            {
                "pattern": "**/*.py",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    grep_payload = json.loads(
        handlers["grep_files"].invoke(
            {
                "pattern": "return a \\+ b",
                "path": "/mnt/user-data/workspace",
                "file_glob": "**/*.py",
                "context": 1,
            }
        )
    )

    assert glob_payload["target"] == "files"
    assert glob_payload["files"] == ["/mnt/user-data/workspace/src/calculator.py"]
    assert grep_payload["target"] == "content"
    assert grep_payload["matches"][0]["path"] == "/mnt/user-data/workspace/src/calculator.py"
    assert grep_payload["matches"][0]["line"] == 2
    assert grep_payload["stats"]["ignored_dirs"] >= 1


def test_search_files_accepts_file_glob_only_for_model_recovery(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-search-glob",
        effective_config_fingerprint="cfg-search-glob",
    )
    workspace = path_service.thread_workspace_dir("thread-search-glob")
    (workspace / "docs").mkdir(parents=True)
    (workspace / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (workspace / "docs" / "guide.txt").write_text("Guide\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["search_files"].invoke(
            {
                "file_glob": "*.md",
                "path": "/mnt/user-data/workspace/docs",
            }
        )
    )

    assert payload["target"] == "files"
    assert payload["files"] == ["/mnt/user-data/workspace/docs/guide.md"]


def test_search_files_stops_file_name_scan_at_runtime_budget(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(file_search_module, "DEFAULT_MAX_SCANNED_FILES", 5)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-search-budget",
        effective_config_fingerprint="cfg-search-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-search-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(20):
        (workspace / f"match_{index:02d}.txt").write_text("match\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["search_files"].invoke(
            {
                "pattern": "*.txt",
                "target": "files",
                "path": "/mnt/user-data/workspace",
                "limit": 2,
            }
        )
    )

    assert payload["returned_count"] == 2
    assert payload["truncated"] is True
    assert payload["stats"]["scan_truncated"] is True
    assert payload["stats"]["files_scanned"] == 5
    assert payload["stats"]["max_scanned_files"] == 5


def test_grep_files_stops_no_match_scan_at_runtime_budget(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(file_search_module, "DEFAULT_MAX_SCANNED_FILES", 4)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-grep-budget",
        effective_config_fingerprint="cfg-grep-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-grep-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (workspace / f"candidate_{index:02d}.txt").write_text("ordinary content\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["grep_files"].invoke(
            {
                "pattern": "never-present",
                "path": "/mnt/user-data/workspace",
                "limit": 2,
            }
        )
    )

    assert payload["matches"] == []
    assert payload["truncated"] is True
    assert payload["next_offset"] == 0
    assert payload["stats"]["scan_truncated"] is True
    assert payload["stats"]["files_scanned"] == 4
    assert payload["stats"]["max_scanned_files"] == 4


def test_code_map_builds_cached_coding_graph_and_invalidates_on_file_change(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )
    workspace = path_service.thread_workspace_dir("thread-1")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "app.py").write_text("from pkg.maths import add\n\nprint(add(1, 2))\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    first = json.loads(
        handlers["code_map"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "focus": "pkg/maths.py",
            }
        )
    )
    second = json.loads(
        handlers["code_map"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "focus": "pkg/maths.py",
            }
        )
    )
    (workspace / "pkg" / "maths.py").write_text(
        "def add(a, b):\n    return a + b\n\nclass Calculator:\n    pass\n",
        encoding="utf-8",
    )
    changed = json.loads(
        handlers["code_map"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "focus": "pkg/maths.py",
            }
        )
    )

    assert first["cache"] == "miss"
    assert second["cache"] == "hit"
    assert changed["cache"] == "miss"
    assert first["fingerprint"] != changed["fingerprint"]
    assert first["focus"]["matched"] is True
    assert "/mnt/user-data/workspace/app.py" in first["focus"]["dependents"]
    assert any(node["path"] == "/mnt/user-data/workspace/pkg/maths.py" for node in first["nodes"])
    assert "symbol_count" in first["nodes"][0]
    assert "security" not in first


def test_code_map_stops_project_scan_at_runtime_budget(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(code_map_module, "DEFAULT_CODE_ANALYSIS_SCAN_PATH_LIMIT", 5, raising=False)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-map-scan-budget",
        effective_config_fingerprint="cfg-code-map-scan-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-code-map-scan-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(20):
        (workspace / f"module_{index:02d}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["code_map"].invoke(
            {
                "path": "/mnt/user-data/workspace",
                "max_files": 20,
            }
        )
    )

    assert payload["stats"]["scan_truncated"] is True
    assert payload["stats"]["scanned_path_count"] == 5
    assert payload["stats"]["max_scanned_paths"] == 5
    assert payload["file_count"] <= 5


def test_lsp_workspace_probe_stops_scan_at_runtime_budget(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(code_semantics_module, "DEFAULT_LSP_SCAN_PATH_LIMIT", 4, raising=False)
    path_service = PathService(contract_tmp_path)
    workspace = path_service.thread_workspace_dir("thread-lsp-scan-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (workspace / f"module_{index:02d}.py").write_text(f"VALUE_{index} = {index}\n", encoding="utf-8")

    probe = code_semantics_module.lsp_workspace_probe(
        path_service=path_service,
        thread_id="thread-lsp-scan-budget",
        path="/mnt/user-data/workspace",
        max_files=20,
    )

    assert probe.snapshot.scan_truncated is True
    assert probe.snapshot.scanned_path_count == 4
    assert probe.snapshot.max_scanned_paths == 4
    assert len(probe.files) <= 4


def test_code_symbols_reports_shared_analyzer_scan_budget(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(code_map_module, "DEFAULT_CODE_ANALYSIS_SCAN_PATH_LIMIT", 3, raising=False)
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-symbols-scan-budget",
        effective_config_fingerprint="cfg-code-symbols-scan-budget",
    )
    workspace = path_service.thread_workspace_dir("thread-code-symbols-scan-budget")
    workspace.mkdir(parents=True, exist_ok=True)
    for index in range(10):
        (workspace / f"module_{index:02d}.py").write_text(f"def fn_{index}():\n    return {index}\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    payload = json.loads(
        handlers["code_symbols"].invoke(
            {
                "focus": "module_09.py",
                "path": "/mnt/user-data/workspace",
                "max_files": 10,
            }
        )
    )

    assert payload["scan_truncated"] is True
    assert payload["scanned_path_count"] == 3
    assert payload["max_scanned_paths"] == 3


def test_coding_tools_accept_project_path_for_nested_projects(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-nested-projects",
        effective_config_fingerprint="cfg-nested-projects",
    )
    workspace = path_service.thread_workspace_dir("thread-nested-projects")
    (workspace / "alpha" / "pkg").mkdir(parents=True)
    (workspace / "beta" / "pkg").mkdir(parents=True)
    (workspace / "alpha" / "pkg" / "shared.py").write_text("def alpha_only():\n    return 'alpha'\n", encoding="utf-8")
    (workspace / "beta" / "pkg" / "shared.py").write_text("def beta_only():\n    return 'beta'\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    code_map = json.loads(handlers["code_map"].invoke({"project_path": "/mnt/user-data/workspace/beta"}))
    symbols = json.loads(
        handlers["code_symbols"].invoke(
            {
                "project_path": "/mnt/user-data/workspace/beta",
                "focus": "pkg/shared.py",
            }
        )
    )
    summary = json.loads(
        handlers["code_file_summary"].invoke(
            {
                "project_path": "/mnt/user-data/workspace/beta",
                "file_path": "pkg/shared.py",
            }
        )
    )

    assert code_map["root"] == "/mnt/user-data/workspace/beta"
    assert {node["relative_path"] for node in code_map["nodes"]} == {"pkg/shared.py"}
    assert code_map["nodes"][0]["path"] == "/mnt/user-data/workspace/beta/pkg/shared.py"
    assert [symbol["name"] for symbol in symbols["symbols"]] == ["beta_only"]
    assert summary["path"] == "/mnt/user-data/workspace/beta/pkg/shared.py"
    assert summary["symbols"][0]["name"] == "beta_only"


def test_split_coding_tools_return_bounded_specialized_payloads(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-tools",
        effective_config_fingerprint="cfg-code-tools",
    )
    workspace = path_service.thread_workspace_dir("thread-code-tools")
    (workspace / ".github").mkdir(parents=True)
    (workspace / ".github" / "CODEOWNERS").write_text("pkg/* @core-team\n", encoding="utf-8")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (workspace / "pkg" / "maths.py").write_text(
        "\n".join(
            [
                "def add(a, b):",
                "    print('debug')",
                "    return a + b",
                "",
                "def unsafe(query, conn):",
                "    return conn.execute(f'SELECT * FROM users WHERE name = {query}')",
            ]
        ),
        encoding="utf-8",
    )
    (workspace / "app.py").write_text("from pkg.maths import add\n\nprint(add(1, 2))\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Docs\n\nSee [[Guide]] and [Missing](missing.md).\n", encoding="utf-8")
    (workspace / "Guide.md").write_text("# Guide\n\nBack to [README](README.md).\n", encoding="utf-8")
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    focus = json.loads(handlers["code_focus"].invoke({"focus": "pkg/maths.py"}))
    security = json.loads(handlers["code_security_scan"].invoke({"severity": "high"}))
    patterns = json.loads(handlers["code_pattern_scan"].invoke({"limit": 10}))
    docs = json.loads(handlers["code_doc_graph"].invoke({"limit": 10}))
    health = json.loads(handlers["code_health"].invoke({}))
    symbols = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    symbol_search = json.loads(handlers["code_symbol_search"].invoke({"query": "add"}))
    definition = json.loads(handlers["code_definition"].invoke({"symbol_name": "add", "context": 0}))
    references = json.loads(handlers["code_references"].invoke({"symbol_name": "add", "context": 0}))
    file_summary = json.loads(handlers["code_file_summary"].invoke({"file_path": "pkg/maths.py"}))
    impact = json.loads(
        handlers["code_impact"].invoke(
            {
                "target_path": "pkg/maths.py",
                "symbol_name": "add",
                "limit": 10,
            }
        )
    )

    assert focus["focus"]["matched"] is True
    assert focus["focus"]["path"] == "/mnt/user-data/workspace/pkg/maths.py"
    focus_related_by_path = {item["path"]: item for item in focus["related_files"]}
    assert focus_related_by_path["/mnt/user-data/workspace/app.py"]["path"] == "/mnt/user-data/workspace/app.py"
    assert focus["focus"]["symbols"][0]["name"] == "add"
    assert security["summary"]["severity_counts"]["high"] >= 1
    assert security["findings"][0]["kind"] == "sql_injection_risk"
    assert "nodes" not in security
    assert "summary" in patterns
    assert docs["total_broken_links"] == 1
    assert docs["total_edges"] == 2
    assert health["health"]["grade"] in {"good", "watch", "needs_attention"}
    assert health["doc_broken_link_count"] == 1
    assert [symbol["name"] for symbol in symbols["symbols"]] == ["add", "unsafe"]
    assert symbols["semantic_backend"] == "static"
    assert symbol_search["matches"][0]["path"] == "/mnt/user-data/workspace/pkg/maths.py"
    assert symbol_search["semantic_backend"] == "static"
    assert definition["definitions"][0]["relative_path"] == "pkg/maths.py"
    assert definition["definitions"][0]["line"] == 1
    assert definition["semantic_backend"] == "static"
    assert any(item["relative_path"] == "app.py" for item in references["references"])
    assert references["semantic_backend"] == "static"
    assert file_summary["relative_path"] == "pkg/maths.py"
    assert file_summary["symbols_total"] == 2
    assert "security_findings" in file_summary
    assert impact["matched"] is True
    assert impact["target"]["relative_path"] == "pkg/maths.py"
    assert impact["impact"]["dependents_total"] == 1
    assert impact["references"]["files_total"] >= 2
    assert any(item["relative_path"] == "app.py" for item in impact["references"]["files"])
    assert any(item["relative_path"] == "pkg/maths.py" for item in impact["references"]["files"])
    assert any(call["tool"] == "code_references" for call in impact["suggested_next_tools"])


def test_code_references_counts_beyond_return_limit_without_expanding_context(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-reference-counts",
        effective_config_fingerprint="cfg-code-reference-counts",
    )
    workspace = path_service.thread_workspace_dir("thread-code-reference-counts")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text(
        "\n".join(["def add(a, b):", "    return a + b"]),
        encoding="utf-8",
    )
    for index in range(5):
        (workspace / f"use_{index}.py").write_text(
            "\n".join(
                [
                    "from pkg.maths import add",
                    f"VALUE_{index} = add({index}, {index})",
                ]
            ),
            encoding="utf-8",
        )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    references = json.loads(handlers["code_references"].invoke({"symbol_name": "add", "limit": 3, "context": 0}))
    impact = json.loads(
        handlers["code_impact"].invoke(
            {
                "target_path": "pkg/maths.py",
                "symbol_name": "add",
                "limit": 3,
            }
        )
    )

    assert references["returned"] == 3
    assert references["total_estimate"] > references["returned"]
    assert references["truncated"] is True
    assert all(item["context"] == [] for item in references["references"])
    assert impact["references"]["files_total"] == 3
    assert impact["references"]["files_truncated"] is True
    assert impact["references"]["files"][0]["files_total_estimate"] > impact["references"]["files_total"]


def test_code_semantics_can_use_external_index_and_fallback_to_static(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-external-index")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    index_path = contract_tmp_path / "semantic-index.json"
    index_path.write_text(
        json.dumps(
            {
                "root": "/mnt/user-data/workspace",
                "fingerprint": "external-v1",
                "nodes": [
                    {
                        "path": "/mnt/user-data/workspace/pkg/external.py",
                        "relative_path": "pkg/external.py",
                        "language": "python",
                        "symbols": [{"name": "external_add", "kind": "function", "line": 7}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _, external_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-external-index",
        code_semantics_config=CodeSemanticsConfig(
            backend="external_index",
            external_index_path=str(index_path),
            validate_freshness=False,
        ),
        effective_config_fingerprint="cfg-code-external-index",
    )
    external_handlers = {entry.name: entry.handler for entry in external_bundle.visible_tools}

    external = json.loads(external_handlers["code_symbol_search"].invoke({"query": "external"}))

    assert external["semantic_backend"] == "external_index"
    assert external["semantic_index_freshness"] == "unchecked"
    assert external["fingerprint"] == "external-v1"
    assert external["matches"][0]["relative_path"] == "pkg/external.py"

    _, fallback_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-external-index",
        code_semantics_config=CodeSemanticsConfig(
            backend="external_index",
            external_index_path=str(contract_tmp_path / "missing-index.json"),
        ),
        effective_config_fingerprint="cfg-code-external-fallback",
    )
    fallback_handlers = {entry.name: entry.handler for entry in fallback_bundle.visible_tools}

    fallback = json.loads(fallback_handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))

    assert fallback["semantic_backend"] == "external_index->fallback:static"
    assert [symbol["name"] for symbol in fallback["symbols"]] == ["add"]

    _, stale_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-external-index",
        code_semantics_config=CodeSemanticsConfig(
            backend="external_index",
            external_index_path=str(index_path),
        ),
        effective_config_fingerprint="cfg-code-external-stale-fallback",
    )
    stale_handlers = {entry.name: entry.handler for entry in stale_bundle.visible_tools}

    stale = json.loads(stale_handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))

    assert stale["semantic_backend"] == "external_index->stale:fallback:static"
    assert stale["semantic_index_freshness"] == "stale"
    assert stale["current_fingerprint"]
    assert [symbol["name"] for symbol in stale["symbols"]] == ["add"]


def test_code_semantic_index_writes_validates_and_can_be_reused_as_external_backend(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-semantic-index")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "app.py").write_text("from pkg.maths import add\n\nVALUE = add(1, 2)\n", encoding="utf-8")
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-semantic-index",
        effective_config_fingerprint="cfg-code-semantic-index",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    written = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "output_path": "/mnt/user-data/outputs/custom-code-index.json",
                "max_files": 50,
            }
        )
    )
    validated = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "validate",
                "output_path": "/mnt/user-data/outputs/custom-code-index.json",
            }
        )
    )

    assert written["valid"] is True
    assert written["fresh"] is True
    assert written["freshness"] == "fresh"
    assert written["path"] == "/mnt/user-data/outputs/custom-code-index.json"
    assert written["node_count"] == 2
    assert written["symbol_count"] == 1
    assert validated["valid"] is True
    assert validated["fresh"] is True
    assert validated["fingerprint"] == written["fingerprint"]

    _, external_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-semantic-index",
        code_semantics_config=CodeSemanticsConfig(
            backend="external_index",
            external_index_path="/mnt/user-data/outputs/custom-code-index.json",
        ),
        effective_config_fingerprint="cfg-code-semantic-index-external",
    )
    external_handlers = {entry.name: entry.handler for entry in external_bundle.visible_tools}
    search = json.loads(external_handlers["code_symbol_search"].invoke({"query": "add"}))

    assert search["semantic_backend"] == "external_index"
    assert search["semantic_index_freshness"] == "fresh"
    assert search["fingerprint"] == written["fingerprint"]
    assert search["matches"][0]["relative_path"] == "pkg/maths.py"

    (workspace / "pkg" / "maths.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    stale_validation = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "validate",
                "output_path": "/mnt/user-data/outputs/custom-code-index.json",
            }
        )
    )
    stale_search = json.loads(external_handlers["code_symbol_search"].invoke({"query": "subtract"}))

    assert stale_validation["valid"] is True
    assert stale_validation["fresh"] is False
    assert stale_validation["freshness"] == "stale"
    assert stale_validation["current_fingerprint"] != stale_validation["fingerprint"]
    assert stale_search["semantic_backend"] == "external_index->stale:fallback:static"
    assert stale_search["semantic_index_freshness"] == "stale"
    assert stale_search["matches"][0]["name"] == "subtract"

    stale_health = json.loads(external_handlers["code_semantic_index"].invoke({"mode": "health"}))
    assert stale_health["external_index"]["fresh"] is False
    assert stale_health["external_index"]["freshness"] == "stale"
    assert stale_health["external_index"]["drift"]["changed_paths"] == ["pkg/maths.py"]
    assert "mode=refresh" in stale_health["external_index"]["recommendation"]
    recovered = json.loads(external_handlers["code_semantic_index"].invoke({"mode": "recover"}))

    refreshed = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "refresh",
                "output_path": "/mnt/user-data/outputs/custom-code-index.json",
            }
        )
    )
    fresh_after_refresh = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "refresh",
                "output_path": "/mnt/user-data/outputs/custom-code-index.json",
            }
        )
    )

    assert recovered["mode"] == "recover"
    assert recovered["backend"] == "external_index"
    assert recovered["recovery"] == "external_index_refresh"
    assert recovered["action"] == "rewritten"
    assert recovered["fresh"] is True
    assert recovered["previous_fresh"] is False
    assert recovered["previous_drift"]["changed_paths"] == ["pkg/maths.py"]
    assert refreshed["action"] == "kept"
    assert refreshed["fresh"] is True
    assert refreshed["drift"]["changed_paths"] == []
    assert fresh_after_refresh["action"] == "kept"
    assert fresh_after_refresh["fresh"] is True


def test_code_semantic_index_health_reports_static_backend_without_reading_index(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-semantic-health-static",
        effective_config_fingerprint="cfg-code-semantic-health-static",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))

    assert health["mode"] == "health"
    assert health["backend"] == "static"
    assert health["fallback_to_static"] is True
    assert "lsp_jsonrpc" not in health


def test_code_semantic_index_watch_detects_manual_workspace_edits(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-watch-static")
    (workspace / "pkg").mkdir(parents=True)
    source_file = workspace / "pkg" / "maths.py"
    source_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-watch-static",
        effective_config_fingerprint="cfg-code-watch-static",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    started = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "start",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    unchanged = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "poll",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    source_file.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef subtract(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    changed = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "poll",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    status = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "status",
                "path": "/mnt/user-data/workspace",
            }
        )
    )
    stopped = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "stop",
                "path": "/mnt/user-data/workspace",
            }
        )
    )

    assert started["watching"] is True
    assert started["changed"] is False
    assert unchanged["changed"] is False
    assert changed["changed"] is True
    assert changed["drift"]["changed_paths"] == ["pkg/maths.py"]
    assert "content" in changed["drift"]["changed_details"][0]["reasons"]
    assert changed["drift"]["changed_details"][0]["symbol_delta"]["added"] == ["subtract"]
    assert changed["recovery"] is None
    assert status["watching"] is True
    assert status["poll_count"] == 2
    assert stopped["cleared"] is True
    assert str(workspace) not in json.dumps(changed, ensure_ascii=False)


def test_code_semantic_index_watch_auto_recovers_external_index(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-watch-external")
    (workspace / "pkg").mkdir(parents=True)
    source_file = workspace / "pkg" / "maths.py"
    source_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    _, writer_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-watch-external",
        effective_config_fingerprint="cfg-code-watch-external-writer",
    )
    writer_handlers = {entry.name: entry.handler for entry in writer_bundle.visible_tools}
    writer_handlers["code_semantic_index"].invoke(
        {
            "mode": "write",
            "output_path": "/mnt/user-data/outputs/watch-code-index.json",
            "max_files": 50,
        }
    )
    _, external_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-watch-external",
        code_semantics_config=CodeSemanticsConfig(
            backend="external_index",
            external_index_path="/mnt/user-data/outputs/watch-code-index.json",
            watch_default_auto_recover=True,
        ),
        effective_config_fingerprint="cfg-code-watch-external",
    )
    handlers = {entry.name: entry.handler for entry in external_bundle.visible_tools}

    handlers["code_semantic_index"].invoke(
        {
            "mode": "watch",
            "watch_action": "start",
            "output_path": "/mnt/user-data/outputs/watch-code-index.json",
            "max_files": 50,
        }
    )
    source_file.write_text(
        "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    changed = json.loads(
        handlers["code_semantic_index"].invoke(
            {
                "mode": "watch",
                "watch_action": "poll",
                "output_path": "/mnt/user-data/outputs/watch-code-index.json",
                "max_files": 50,
            }
        )
    )
    search = json.loads(handlers["code_symbol_search"].invoke({"query": "multiply", "max_files": 50}))

    assert changed["changed"] is True
    assert changed["auto_recover"] is True
    assert changed["recovery"]["backend"] == "external_index"
    assert changed["recovery"]["recovery"] == "external_index_refresh"
    assert changed["recovery"]["fresh"] is True
    assert changed["recovery"]["previous_fresh"] is False
    assert changed["recovery"]["previous_drift"]["changed_paths"] == ["pkg/maths.py"]
    assert search["semantic_backend"] == "external_index"
    assert search["semantic_index_freshness"] == "fresh"
    assert search["matches"][0]["name"] == "multiply"
    assert str(workspace) not in json.dumps(changed, ensure_ascii=False)


def test_code_semantics_can_use_lsp_jsonrpc_backend_and_fallback_to_static(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-jsonrpc")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "app.py").write_text("from pkg.maths import add\n\nVALUE = add(1, 2)\n", encoding="utf-8")
    fake_lsp = contract_tmp_path / "fake_lsp.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {
                                "capabilities": {
                                    "documentSymbolProvider": True,
                                    "definitionProvider": True,
                                    "referencesProvider": True,
                                }
                            },
                        }
                    )
                elif method == "textDocument/documentSymbol":
                    uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    if uri.endswith("/app.py"):
                        result = [
                            {
                                "name": "VALUE",
                                "kind": 14,
                                "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                                "selectionRange": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                            }
                        ]
                    else:
                        result = [
                            {
                                "name": "LspMath",
                                "kind": 5,
                                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 7}},
                                "selectionRange": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 7}},
                                "children": [
                                    {
                                        "name": "lsp_add",
                                        "kind": 12,
                                        "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}},
                                        "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}},
                                    }
                                ],
                            }
                        ]
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
                elif method == "textDocument/references":
                    target_uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    app_uri = target_uri.rsplit("/pkg/maths.py", 1)[0] + "/app.py"
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": [
                                {"uri": target_uri, "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}}},
                                {"uri": app_uri, "range": {"start": {"line": 2, "character": 8}, "end": {"line": 2, "character": 15}}},
                            ],
                        }
                    )
                elif method == "textDocument/definition":
                    target_uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"uri": target_uri, "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 11}}},
                        }
                    )
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).strip(),
        encoding="utf-8",
    )

    _, lsp_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-jsonrpc",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-jsonrpc",
    )
    lsp_handlers = {entry.name: entry.handler for entry in lsp_bundle.visible_tools}

    lsp_search = json.loads(lsp_handlers["code_symbol_search"].invoke({"query": "lsp_add"}))
    lsp_symbols = json.loads(lsp_handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    lsp_definition = json.loads(
        lsp_handlers["code_definition"].invoke(
            {
                "symbol_name": "lsp_add",
                "file_path": "pkg/maths.py",
                "context": 1,
            }
        )
    )
    lsp_references = json.loads(
        lsp_handlers["code_references"].invoke(
            {
                "symbol_name": "lsp_add",
                "file_path": "pkg/maths.py",
                "context": 1,
            }
        )
    )

    assert lsp_search["semantic_backend"] == "lsp_jsonrpc"
    assert lsp_search["semantic_index_freshness"] == "current"
    assert lsp_search["matches"][0]["name"] == "lsp_add"
    assert [symbol["name"] for symbol in lsp_symbols["symbols"]] == ["LspMath", "lsp_add"]
    assert lsp_definition["semantic_definition_backend"] == "lsp_jsonrpc"
    assert lsp_definition["definitions"][0]["relative_path"] == "pkg/maths.py"
    assert lsp_definition["definitions"][0]["context"]
    assert lsp_references["semantic_reference_backend"] == "lsp_jsonrpc"
    assert lsp_references["returned"] == 2
    assert [item["relative_path"] for item in lsp_references["references"]] == ["app.py", "pkg/maths.py"]
    assert lsp_references["references"][0]["context"]

    _, fallback_bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-jsonrpc",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(contract_tmp_path / "missing_lsp.py")],
        ),
        effective_config_fingerprint="cfg-code-lsp-jsonrpc-fallback",
    )
    fallback_handlers = {entry.name: entry.handler for entry in fallback_bundle.visible_tools}

    fallback = json.loads(fallback_handlers["code_symbol_search"].invoke({"query": "add"}))

    assert fallback["semantic_backend"] == "lsp_jsonrpc->fallback:static"
    assert fallback["semantic_index_freshness"] == "unavailable"
    assert fallback["matches"][0]["name"] == "add"
    close_lsp_session_pool()


def test_code_semantics_lsp_capability_probe_skips_unsupported_methods(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-capability-probe")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "app.py").write_text("from pkg.maths import add\n\nVALUE = add(1, 2)\n", encoding="utf-8")
    fake_lsp = contract_tmp_path / "fake_lsp_symbols_only.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import sys

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
                elif method == "textDocument/documentSymbol":
                    uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    if uri.endswith("/app.py"):
                        result = [
                            {
                                "name": "VALUE",
                                "kind": 14,
                                "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                                "selectionRange": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                            }
                        ]
                    else:
                        result = [
                            {
                                "name": "add",
                                "kind": 12,
                                "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                            }
                        ]
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": result,
                        }
                    )
                elif method in {"textDocument/definition", "textDocument/references"}:
                    raise SystemExit(f"unsupported method was called: {method}")
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-capability-probe",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-capability-probe",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    definition = json.loads(handlers["code_definition"].invoke({"symbol_name": "add", "context": 0}))
    references = json.loads(handlers["code_references"].invoke({"symbol_name": "add", "context": 0}))

    assert definition["semantic_backend"] == "lsp_jsonrpc"
    assert "semantic_definition_backend" not in definition
    assert definition["definitions"][0]["relative_path"] == "pkg/maths.py"
    assert references["semantic_backend"] == "lsp_jsonrpc"
    assert "semantic_reference_backend" not in references
    assert any(item["relative_path"] == "app.py" for item in references["references"])
    close_lsp_session_pool()


def test_code_semantics_lsp_jsonrpc_reuses_session_for_followup_calls(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-reuse")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workspace / "app.py").write_text("from pkg.maths import add\n\nVALUE = add(1, 2)\n", encoding="utf-8")
    init_count = contract_tmp_path / "lsp-init-count.txt"
    fake_lsp = contract_tmp_path / "fake_lsp_reuse.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import pathlib
            import sys
            import urllib.parse

            INIT_COUNT = pathlib.Path(__INIT_COUNT__)

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            def increment_init_count():
                count = int(INIT_COUNT.read_text(encoding="utf-8")) if INIT_COUNT.exists() else 0
                INIT_COUNT.write_text(str(count + 1), encoding="utf-8")

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    increment_init_count()
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {
                                "capabilities": {
                                    "documentSymbolProvider": True,
                                    "definitionProvider": True,
                                    "referencesProvider": True,
                                }
                            },
                        }
                    )
                elif method == "textDocument/documentSymbol":
                    uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    if uri.endswith("/app.py"):
                        result = [
                            {
                                "name": "VALUE",
                                "kind": 14,
                                "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                                "selectionRange": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
                            }
                        ]
                    else:
                        result = [
                            {
                                "name": "add",
                                "kind": 12,
                                "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                            }
                        ]
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
                elif method == "textDocument/definition":
                    target_uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": {"uri": target_uri, "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}}},
                        }
                    )
                elif method == "textDocument/references":
                    target_uri = message.get("params", {}).get("textDocument", {}).get("uri", "")
                    app_uri = target_uri.rsplit("/pkg/maths.py", 1)[0] + "/app.py"
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": [
                                {"uri": target_uri, "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}}},
                                {"uri": app_uri, "range": {"start": {"line": 2, "character": 8}, "end": {"line": 2, "character": 15}}},
                            ],
                        }
                    )
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).replace("__INIT_COUNT__", json.dumps(str(init_count))).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-reuse",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-reuse",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    symbols = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    definition = json.loads(handlers["code_definition"].invoke({"symbol_name": "add", "file_path": "pkg/maths.py", "context": 0}))
    references = json.loads(handlers["code_references"].invoke({"symbol_name": "add", "file_path": "pkg/maths.py", "context": 0}))

    assert symbols["semantic_backend"] == "lsp_jsonrpc"
    assert definition["semantic_definition_backend"] == "lsp_jsonrpc"
    assert references["semantic_reference_backend"] == "lsp_jsonrpc"
    assert init_count.read_text(encoding="utf-8") == "1"
    health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))
    lsp_health = health["lsp_jsonrpc"]
    assert health["backend"] == "lsp_jsonrpc"
    assert lsp_health["configured"] is True
    assert lsp_health["command_configured"] is True
    assert lsp_health["command_size"] == 2
    assert lsp_health["env_keys_count"] == 0
    assert lsp_health["pool"]["running_session_count"] == 1
    assert lsp_health["pool"]["sessions"][0]["capabilities"] == {
        "document_symbols": True,
        "definitions": True,
        "references": True,
    }
    assert str(fake_lsp) not in json.dumps(health, ensure_ascii=False)
    close_lsp_session_pool()


def test_code_semantics_lsp_jsonrpc_restarts_session_when_workspace_changes(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-freshness")
    (workspace / "pkg").mkdir(parents=True)
    source_file = workspace / "pkg" / "maths.py"
    source_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    init_count = contract_tmp_path / "lsp-freshness-init-count.txt"
    fake_lsp = contract_tmp_path / "fake_lsp_freshness.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import pathlib
            import sys
            import urllib.parse

            INIT_COUNT = pathlib.Path(__INIT_COUNT__)

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            def increment_init_count():
                count = int(INIT_COUNT.read_text(encoding="utf-8")) if INIT_COUNT.exists() else 0
                INIT_COUNT.write_text(str(count + 1), encoding="utf-8")

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    increment_init_count()
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
                elif method == "textDocument/documentSymbol":
                    uri = message["params"]["textDocument"]["uri"]
                    parsed = urllib.parse.urlparse(uri)
                    path_text = urllib.parse.unquote(parsed.path)
                    if len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
                        path_text = path_text[1:]
                    text = pathlib.Path(path_text).read_text(encoding="utf-8")
                    name = "mul" if "def mul" in text else "add"
                    result = [
                        {
                            "name": name,
                            "kind": 12,
                            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                            "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                        }
                    ]
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).replace("__INIT_COUNT__", json.dumps(str(init_count))).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-freshness",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-freshness",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    first = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    source_file.write_text("def mul(a, b):\n    return a * b\n\nVALUE = mul(2, 3)\n", encoding="utf-8")
    second = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))
    session = health["lsp_jsonrpc"]["pool"]["sessions"][0]

    assert [symbol["name"] for symbol in first["symbols"]] == ["add"]
    assert [symbol["name"] for symbol in second["symbols"]] == ["mul"]
    assert init_count.read_text(encoding="utf-8") == "2"
    assert session["workspace_file_count"] == 1
    assert "workspace_fingerprint_hash" in session
    assert str(workspace) not in json.dumps(health, ensure_ascii=False)
    close_lsp_session_pool()


def test_code_semantics_lsp_jsonrpc_health_reports_stale_session_before_restart(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-health-freshness")
    (workspace / "pkg").mkdir(parents=True)
    source_file = workspace / "pkg" / "maths.py"
    source_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    init_count = contract_tmp_path / "lsp-health-freshness-init-count.txt"
    fake_lsp = contract_tmp_path / "fake_lsp_health_freshness.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import pathlib
            import sys
            import urllib.parse

            INIT_COUNT = pathlib.Path(__INIT_COUNT__)

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            def increment_init_count():
                count = int(INIT_COUNT.read_text(encoding="utf-8")) if INIT_COUNT.exists() else 0
                INIT_COUNT.write_text(str(count + 1), encoding="utf-8")

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    increment_init_count()
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
                elif method == "textDocument/documentSymbol":
                    uri = message["params"]["textDocument"]["uri"]
                    parsed = urllib.parse.urlparse(uri)
                    path_text = urllib.parse.unquote(parsed.path)
                    if len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
                        path_text = path_text[1:]
                    text = pathlib.Path(path_text).read_text(encoding="utf-8")
                    name = "mul" if "def mul" in text else "add"
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": [
                                {
                                    "name": name,
                                    "kind": 12,
                                    "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                    "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                }
                            ],
                        }
                    )
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).replace("__INIT_COUNT__", json.dumps(str(init_count))).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-health-freshness",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-health-freshness",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    first = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    source_file.write_text("def mul(a, b):\n    return a * b\n\nVALUE = mul(2, 3)\n", encoding="utf-8")
    stale_health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))
    stale_session = stale_health["lsp_jsonrpc"]["pool"]["sessions"][0]
    init_count_after_health = init_count.read_text(encoding="utf-8")
    second = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    fresh_health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))
    fresh_session = fresh_health["lsp_jsonrpc"]["pool"]["sessions"][0]

    assert [symbol["name"] for symbol in first["symbols"]] == ["add"]
    assert stale_health["lsp_jsonrpc"]["workspace_probe"]["available"] is True
    assert stale_session["workspace_fresh"] is False
    assert stale_session["workspace_freshness"] == "stale"
    assert stale_session["needs_restart"] is True
    assert stale_session["current_workspace_file_count"] == 1
    assert stale_session["workspace_fingerprint_hash"] != stale_session["current_workspace_fingerprint_hash"]
    assert init_count_after_health == "1"
    assert [symbol["name"] for symbol in second["symbols"]] == ["mul"]
    assert init_count.read_text(encoding="utf-8") == "2"
    assert fresh_session["workspace_fresh"] is True
    assert fresh_session["workspace_freshness"] == "fresh"
    assert fresh_session["needs_restart"] is False
    assert str(workspace) not in json.dumps(stale_health, ensure_ascii=False)
    close_lsp_session_pool()


def test_code_semantic_index_recover_discards_stale_lsp_session(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-recover")
    (workspace / "pkg").mkdir(parents=True)
    source_file = workspace / "pkg" / "maths.py"
    source_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    init_count = contract_tmp_path / "lsp-recover-init-count.txt"
    fake_lsp = contract_tmp_path / "fake_lsp_recover.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import pathlib
            import sys
            import urllib.parse

            INIT_COUNT = pathlib.Path(__INIT_COUNT__)

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            def increment_init_count():
                count = int(INIT_COUNT.read_text(encoding="utf-8")) if INIT_COUNT.exists() else 0
                INIT_COUNT.write_text(str(count + 1), encoding="utf-8")

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    increment_init_count()
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
                elif method == "textDocument/documentSymbol":
                    uri = message["params"]["textDocument"]["uri"]
                    parsed = urllib.parse.urlparse(uri)
                    path_text = urllib.parse.unquote(parsed.path)
                    if len(path_text) >= 3 and path_text[0] == "/" and path_text[2] == ":":
                        path_text = path_text[1:]
                    text = pathlib.Path(path_text).read_text(encoding="utf-8")
                    name = "mul" if "def mul" in text else "add"
                    write_message(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "result": [
                                {
                                    "name": name,
                                    "kind": 12,
                                    "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                    "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                                }
                            ],
                        }
                    )
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).replace("__INIT_COUNT__", json.dumps(str(init_count))).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-recover",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
        ),
        effective_config_fingerprint="cfg-code-lsp-recover",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    first = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    source_file.write_text("def mul(a, b):\n    return a * b\n\nVALUE = mul(2, 3)\n", encoding="utf-8")
    recovered = json.loads(handlers["code_semantic_index"].invoke({"mode": "recover"}))
    health_after_recover = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))

    assert [symbol["name"] for symbol in first["symbols"]] == ["add"]
    assert recovered["mode"] == "recover"
    assert recovered["backend"] == "lsp_jsonrpc"
    assert recovered["recovery"] == "lsp_session_recover"
    assert recovered["recovered"] is True
    assert recovered["recovered_session_count"] == 1
    assert recovered["recovered_sessions"][0]["workspace_freshness"] == "stale"
    assert recovered["post_recovery"]["session_count"] == 0
    assert health_after_recover["lsp_jsonrpc"]["pool"]["session_count"] == 0
    assert init_count.read_text(encoding="utf-8") == "1"
    second = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    assert [symbol["name"] for symbol in second["symbols"]] == ["mul"]
    assert init_count.read_text(encoding="utf-8") == "2"
    assert str(workspace) not in json.dumps(recovered, ensure_ascii=False)
    close_lsp_session_pool()


def test_code_semantics_lsp_jsonrpc_reports_sanitized_fallback_diagnostics(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-diagnostics")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    fake_lsp = contract_tmp_path / "fake_lsp_stderr.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import sys

            sys.stderr.write("api_key=sk-secretvalue123456 path=C:\\\\Users\\\\alice\\\\repo\\\\main.py uri=file:///C:/Users/alice/repo/main.py\\n")
            sys.stderr.flush()
            raise SystemExit(9)
            """
        ).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-diagnostics",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
            lsp_timeout_seconds=1,
            lsp_stderr_max_chars=500,
        ),
        effective_config_fingerprint="cfg-code-lsp-diagnostics",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    result = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    diagnostics = "\n".join(result.get("semantic_index_diagnostics") or [])

    assert result["semantic_backend"] == "lsp_jsonrpc->fallback:static"
    assert "lsp_jsonrpc stderr:" in diagnostics
    assert "[REDACTED]" in diagnostics
    assert "sk-secretvalue" not in diagnostics
    assert "alice" not in diagnostics
    assert "pkg/maths.py" not in diagnostics
    health = json.loads(handlers["code_semantic_index"].invoke({"mode": "health"}))
    failures = health["lsp_jsonrpc"]["pool"]["recent_failures"]
    assert failures
    assert "key_hash" in failures[-1]
    assert "sk-secretvalue" not in json.dumps(failures, ensure_ascii=False)
    assert "alice" not in json.dumps(failures, ensure_ascii=False)
    close_lsp_session_pool()


def test_code_semantics_lsp_jsonrpc_expires_idle_session(contract_tmp_path) -> None:
    close_lsp_session_pool()
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    workspace = path_service.thread_workspace_dir("thread-code-lsp-ttl")
    (workspace / "pkg").mkdir(parents=True)
    (workspace / "pkg" / "maths.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    init_count = contract_tmp_path / "lsp-ttl-init-count.txt"
    fake_lsp = contract_tmp_path / "fake_lsp_ttl.py"
    fake_lsp.write_text(
        textwrap.dedent(
            """
            import json
            import pathlib
            import sys

            INIT_COUNT = pathlib.Path(__INIT_COUNT__)

            def read_message():
                content_length = None
                while True:
                    line = sys.stdin.buffer.readline()
                    if not line:
                        return None
                    line = line.strip()
                    if not line:
                        break
                    if line.lower().startswith(b"content-length:"):
                        content_length = int(line.split(b":", 1)[1].strip())
                if content_length is None:
                    return None
                return json.loads(sys.stdin.buffer.read(content_length).decode("utf-8"))

            def write_message(payload):
                body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                sys.stdout.buffer.write(f"Content-Length: {len(body)}\\r\\n\\r\\n".encode("ascii") + body)
                sys.stdout.buffer.flush()

            def increment_init_count():
                count = int(INIT_COUNT.read_text(encoding="utf-8")) if INIT_COUNT.exists() else 0
                INIT_COUNT.write_text(str(count + 1), encoding="utf-8")

            while True:
                message = read_message()
                if message is None:
                    break
                method = message.get("method")
                if "id" not in message:
                    if method == "exit":
                        break
                    continue
                if method == "initialize":
                    increment_init_count()
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": {"capabilities": {"documentSymbolProvider": True}}})
                elif method == "textDocument/documentSymbol":
                    result = [
                        {
                            "name": "add",
                            "kind": 12,
                            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                            "selectionRange": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
                        }
                    ]
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": result})
                elif method == "shutdown":
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
                else:
                    write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
            """
        ).replace("__INIT_COUNT__", json.dumps(str(init_count))).strip(),
        encoding="utf-8",
    )

    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-code-lsp-ttl",
        code_semantics_config=CodeSemanticsConfig(
            backend="lsp_jsonrpc",
            lsp_command=[sys.executable, str(fake_lsp)],
            lsp_session_idle_ttl_seconds=0.001,
        ),
        effective_config_fingerprint="cfg-code-lsp-ttl",
    )
    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    first = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))
    time.sleep(0.02)
    second = json.loads(handlers["code_symbols"].invoke({"focus": "pkg/maths.py"}))

    assert first["semantic_backend"] == "lsp_jsonrpc"
    assert second["semantic_backend"] == "lsp_jsonrpc"
    assert init_count.read_text(encoding="utf-8") == "2"
    close_lsp_session_pool()


def test_tool_handlers_support_root_directory_discovery_and_explicit_contract_text(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}
    descriptions = {entry.name: entry.handler.description for entry in bundle.visible_tools}
    schemas = {entry.name: entry.input_schema or {} for entry in bundle.visible_tools}
    root_list_result = handlers["list_dir"].invoke({"path": "/mnt/user-data"})

    assert json.loads(root_list_result) == ["outputs", "uploads", "workspace"]
    assert "/mnt/user-data/workspace" in descriptions["read_file"]
    assert "/mnt/user-data/uploads" in descriptions["read_file"]
    assert "/mnt/user-data/workspace/_host/<alias>" in descriptions["read_file"]
    assert "stable 1-based line numbers" in descriptions["read_file"]
    assert "metadata for one file or directory" in descriptions["file_info"]
    assert "/mnt/user-data/outputs" in descriptions["write_file"]
    assert "insert_before_anchor" in descriptions["patch_file"]
    assert "dry_run=true" in descriptions["patch_file"]
    assert "Delete one file or directory" in descriptions["delete_path"]
    assert "Move or copy one file/directory" in descriptions["move_path"]
    assert "Create a directory" in descriptions["make_dir"]
    assert "/mnt/user-data" in descriptions["list_dir"]
    assert "_host/<alias>" in descriptions["list_dir"]
    assert "kind, size, mtime" in descriptions["list_dir"]
    assert "Search files by name or content" in descriptions["search_files"]
    assert "compact cached code index" in descriptions["code_map"]
    assert "focus file" in descriptions["code_focus"]
    assert "symbol outline" in descriptions["code_symbols"]
    assert "Search symbol names" in descriptions["code_symbol_search"]
    assert "Find bounded textual references" in descriptions["code_references"]
    assert "Summarize one file" in descriptions["code_file_summary"]
    assert "change impact report" in descriptions["code_impact"]
    assert "checking stale code caches after edits" in descriptions["code_semantic_index"]
    assert "project_path" in schemas["code_map"]["properties"]
    assert "project_path" in schemas["code_symbols"]["properties"]
    assert "project_path" in schemas["code_file_summary"]["properties"]
    assert "project_path" in schemas["code_security_scan"]["properties"]
    assert not any(name.startswith("presentation_") for name in descriptions)
    assert "structured decision" in descriptions["ask_clarification"]
    assert schemas["ask_clarification"]["properties"]["selection_mode"]["enum"] == ["single", "multiple", "text", None]
    assert "Run a shell command inside the thread workspace" in descriptions["run_command"]
    assert "_host/<alias>" in descriptions["run_command"]
    assert schemas["run_command"]["properties"]["timeout_seconds"]["maximum"] == assembly_module.MAX_FOREGROUND_COMMAND_TIMEOUT_SECONDS


def test_run_command_foreground_times_out_and_terminates_process_session(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    process_service = TimeoutRecordingProcessService()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=LocalSandboxProvider(),
        path_service=path_service,
        thread_id="thread-run-timeout",
        process_service=process_service,
        effective_config_fingerprint="cfg-run-timeout",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}
    result = json.loads(
        handlers["run_command"].invoke(
            {
                "command": "sleep 30",
                "cwd": "/mnt/user-data/workspace",
                "timeout_seconds": 1,
            }
        )
    )

    assert process_service.wait_calls == [{"session_id": "proc_test", "timeout_seconds": 1}]
    assert process_service.timeout_calls == ["proc_test"]
    assert result["status"] == "timed_out"
    assert result["timed_out"] is True
    assert result["timeout_seconds"] == 1
    assert "timed out" in result["error"]


def test_process_tool_wait_uses_bounded_default_timeout(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    process_service = TimeoutRecordingProcessService(backend_timeout_seconds=None)
    _, bundle = assemble_runtime_tools(
        sandbox_provider=LocalSandboxProvider(),
        path_service=path_service,
        thread_id="thread-process-wait",
        process_service=process_service,
        effective_config_fingerprint="cfg-process-wait",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}
    result = json.loads(handlers["process"].invoke({"action": "wait", "session_id": "proc_test"}))

    assert process_service.wait_calls == [
        {"session_id": "proc_test", "timeout_seconds": assembly_module.DEFAULT_PROCESS_WAIT_TIMEOUT_SECONDS}
    ]
    assert result["status"] == "running"


def test_static_schema_runtime_tools_use_registry_schema_for_handlers(contract_tmp_path) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-static-schema",
        effective_config_fingerprint="cfg-static-schema",
    )

    entries = {entry.name: entry for entry in bundle.visible_tools}

    for name in STATIC_SCHEMA_RUNTIME_TOOL_NAMES:
        entry = entries[name]
        assert entry.input_schema
        assert entry.handler.name == name
        assert entry.handler.args_schema == entry.input_schema

    assert entries["memory"].input_schema["required"] == ["action", "layer"]
    assert entries["session_search"].input_schema["properties"]["mode"]["enum"] == ["recent", "search", "summarize"]
    assert "target_id" in entries["memory_trace"].input_schema["properties"]


def test_runtime_tool_description_decorator_is_local_and_schema_free(contract_tmp_path) -> None:
    import langchain_core.tools as langchain_tools

    assert assembly_module.tool is not langchain_tools.tool

    _, bundle = assemble_runtime_tools(
        sandbox_provider=LocalSandboxProvider(),
        path_service=PathService(contract_tmp_path),
        thread_id="thread-no-static-decorator",
        effective_config_fingerprint="cfg-no-static-decorator",
    )

    for entry in bundle.visible_tools:
        assert getattr(entry.handler, "description", None), entry.name


def test_static_schema_runtime_tools_do_not_use_from_function(monkeypatch, contract_tmp_path) -> None:
    from langchain_core.tools import StructuredTool

    def fail_from_function(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("static-schema runtime tools should use explicit StructuredTool schemas")

    monkeypatch.setattr(StructuredTool, "from_function", fail_from_function)

    _, bundle = assemble_runtime_tools(
        sandbox_provider=LocalSandboxProvider(),
        path_service=PathService(contract_tmp_path),
        thread_id="thread-static-schema-no-reflect",
        effective_config_fingerprint="cfg-static-schema-no-reflect",
    )

    entries = {entry.name: entry for entry in bundle.visible_tools}
    for name in STATIC_SCHEMA_RUNTIME_TOOL_NAMES:
        entry = entries[name]
        assert entry.handler.name == name
        assert entry.handler.args_schema == entry.input_schema


def test_export_document_tool_writes_to_outputs_and_returns_metadata(contract_tmp_path, monkeypatch) -> None:
    path_service = PathService(contract_tmp_path)
    provider = LocalSandboxProvider()
    _, bundle = assemble_runtime_tools(
        sandbox_provider=provider,
        path_service=path_service,
        thread_id="thread-1",
        effective_config_fingerprint="cfg-1",
    )

    handlers = {entry.name: entry.handler for entry in bundle.visible_tools}

    def fake_export_document_file(*, output_path, content, format, mode, scratch_root, cleanup_intermediates):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"docx-bytes")
        return ExportedDocumentResult(
            output_path=output_path,
            mode=mode,
            format=format,
            provider="test-exporter",
            warnings=("layout fallback",),
            scratch_paths=(scratch_root / "draft.md",),
            cleaned_scratch_paths=(scratch_root / "export-123",),
        )

    monkeypatch.setattr("anvil.tools.assembly.export_document_file", fake_export_document_file)

    result = json.loads(
        handlers["export_document"].invoke(
            {
                "content": "# Resume\n\nBody",
                "output_path": "/mnt/user-data/outputs/resume.docx",
            }
        )
    )

    assert result["output_path"] == "/mnt/user-data/outputs/resume.docx"
    assert result["provider"] == "test-exporter"
    assert result["warnings"] == ["layout fallback"]
    assert result["scratch_paths"] == ["/mnt/user-data/workspace/.anvil-scratch/draft.md"]
    assert result["cleaned_scratch_paths"] == ["/mnt/user-data/workspace/.anvil-scratch/export-123"]
    assert (contract_tmp_path / "thread-1" / "outputs" / "resume.docx").exists()
