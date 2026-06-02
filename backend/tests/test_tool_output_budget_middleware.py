from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage

from anvil.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware
from anvil.config import ToolOutputBudgetConfig
from anvil.runtime.tool_registry import ToolRegistryEntry, ToolSourceKind


class _PathServiceStub:
    def __init__(self, root) -> None:
        self.root = root

    def thread_outputs_dir(self, thread_id: str):
        return self.root / thread_id / "outputs"

    def to_artifact_descriptor(self, thread_id: str, kind: str, relative_path: str):
        return SimpleNamespace(artifact_url=f"artifact://{thread_id}/{kind}/{relative_path}")


def _request(tmp_path, *, output_token_budget: int | None = None):
    entry = ToolRegistryEntry(
        name="long_tool",
        display_name="Long Tool",
        source_kind=ToolSourceKind.BUILTIN,
        source_id="core",
        capability_group="filesystem",
        output_token_budget=output_token_budget,
    )
    context = SimpleNamespace(
        config_result=SimpleNamespace(
            effective_config=SimpleNamespace(
                tool_output_budget=ToolOutputBudgetConfig(
                    default_token_budget=20,
                    hard_token_budget=40,
                    default_char_budget=80,
                    hard_char_budget=160,
                    artifact_directory="tool-results",
                    command_compaction_min_chars=400,
                )
            )
        ),
        capability_bundle=SimpleNamespace(visible_tools=(entry,)),
        thread_id="thread-a",
        path_service=_PathServiceStub(tmp_path),
    )
    return SimpleNamespace(
        runtime=SimpleNamespace(context=context),
        tool_call={"name": "long_tool"},
    )


def test_tool_output_budget_leaves_short_results_unchanged(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    original = ToolMessage(content="short output", tool_call_id="call-1")

    result = middleware.wrap_tool_call(_request(contract_tmp_path), lambda request: original)

    assert result is original


def test_tool_output_budget_preserves_multimodal_tool_messages(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    original = ToolMessage(
        content=[
            {"type": "text", "text": "<view_image>"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA="}},
            {"type": "text", "text": "</view_image>"},
        ],
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=1), lambda request: original)

    assert result is original
    assert isinstance(result.content, list)
    assert result.content[1]["type"] == "image_url"


def test_tool_output_budget_truncates_and_persists_hard_overflow(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    original = ToolMessage(content="line of output\n" * 200, tool_call_id="call-1")

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=10), lambda request: original)

    assert isinstance(result, ToolMessage)
    assert "[tool_output_budget]" in str(result.content)
    assert "artifact://thread-a/outputs/tool-results/" in str(result.content)
    assert list((contract_tmp_path / "thread-a" / "outputs" / "tool-results").glob("long_tool-*.txt"))


def test_tool_output_budget_preserves_valid_json(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    original = ToolMessage(
        content=json.dumps(
            {
                "query": "Northstar",
                "groups": [
                    {
                        "thread_id": "thread-a",
                        "summary": "large summary " * 200,
                    }
                ],
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=20), lambda request: original)

    payload = json.loads(str(result.content))
    assert payload["groups"][0]["thread_id"] == "thread-a"
    assert payload["_tool_output_budget"]["truncated"] is True


def test_tool_output_budget_preserves_catalog_item_ids_when_compacting_large_lists(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    original_items = [
        {
            "skill_id": f"skill-{index:02d}",
            "title": f"Skill {index}",
            "summary": "long summary " * 80,
            "description": "verbose description " * 120,
            "read_tool": "skill_read_file",
        }
        for index in range(12)
    ]
    original = ToolMessage(
        content=json.dumps(
            {
                "total": len(original_items),
                "returned": len(original_items),
                "truncated": False,
                "items": original_items,
                "read_hint": "Use skill_read_file for details.",
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=20), lambda request: original)

    payload = json.loads(str(result.content))
    assert payload["total"] == 12
    assert payload["returned"] == 12
    assert payload["truncated"] is False
    assert [item["skill_id"] for item in payload["items"]] == [f"skill-{index:02d}" for index in range(12)]
    assert all(item["read_tool"] == "skill_read_file" for item in payload["items"])
    assert "_tool_output_budget" in payload


def test_tool_output_budget_compacts_command_output_and_writes_raw_failure_artifact(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "tests/test_alpha.py::test_one PASSED",
            "tests/test_alpha.py::test_two PASSED",
            "tests/test_beta.py::test_fails FAILED",
            "E   AssertionError: expected 1 got 2",
            *[f"tests/test_noise.py::test_{index} PASSED" for index in range(80)],
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 1,
                "command": "pytest -q",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    budget = payload["_tool_output_budget"]
    compaction = budget["compaction"]
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
    assert "tests/test_beta.py::test_fails FAILED" in payload["output"]
    assert "tests/test_noise.py::test_79 PASSED" not in payload["output"]
    assert compaction["profile"] == "test"
    assert compaction["raw_artifact_url"].startswith("artifact://thread-a/outputs/tool-results/")
    assert compaction["savings"]["chars_saved"] > 0
    assert list((contract_tmp_path / "thread-a" / "outputs" / "tool-results").glob("long_tool-raw-*.txt"))


def test_tool_output_budget_writes_raw_artifact_for_successful_compacted_command(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "============================= test session starts =============================",
            "platform linux -- Python 3.12",
            *[f"tests/test_noise.py::test_{index} PASSED" for index in range(100)],
            "100 passed in 12.34s",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "exit_code": 0,
                "command": "pytest -q",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    compaction = payload["_tool_output_budget"]["compaction"]
    artifact_files = list((contract_tmp_path / "thread-a" / "outputs" / "tool-results").glob("long_tool-raw-*.txt"))
    assert payload["status"] == "completed"
    assert payload["exit_code"] == 0
    assert payload["output_compacted"] is True
    assert payload["raw_output_artifact_url"].startswith("artifact://thread-a/outputs/tool-results/")
    assert compaction["raw_artifact_reason"] == "compaction"
    assert "tests/test_noise.py::test_40 PASSED" not in payload["output"]
    assert artifact_files
    assert "tests/test_noise.py::test_40 PASSED" in artifact_files[0].read_text(encoding="utf-8")


def test_tool_output_budget_strips_ansi_and_collapses_progress_noise(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "\x1b[32mDownloading package 1% 1 MB / 100 MB\x1b[0m",
            "\x1b[32mDownloading package 2% 2 MB / 100 MB\x1b[0m",
            "\x1b[32mDownloading package 3% 3 MB / 100 MB\x1b[0m",
            "\x1b[33mnpm WARN deprecated left-pad@1.3.0\x1b[0m",
            *[f"\x1b[32mDownloading package {index}% {index} MB / 100 MB\x1b[0m" for index in range(4, 80)],
            "added 120 packages in 4s",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "exit_code": 0,
                "command": "npm install",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    compaction = payload["_tool_output_budget"]["compaction"]
    artifact_files = list((contract_tmp_path / "thread-a" / "outputs" / "tool-results").glob("long_tool-raw-*.txt"))
    assert "\x1b[" not in payload["output"]
    assert "npm WARN deprecated left-pad@1.3.0" in payload["output"]
    assert "Downloading package 1%" not in payload["output"]
    assert "Downloading package 79%" in payload["output"]
    assert "ansi_removed=" in payload["output"]
    assert "progress_updates_collapsed=" in payload["output"]
    assert compaction["normalization"]["ansi_sequences_removed"] > 0
    assert compaction["normalization"]["progress_updates_collapsed"] > 0
    assert compaction["raw_artifact_reason"] == "compaction"
    assert artifact_files
    assert "\x1b[32mDownloading package 1%" in artifact_files[0].read_text(encoding="utf-8")


def test_tool_output_budget_uses_structured_pytest_filter(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "============================= test session starts =============================",
            "collected 160 items",
            *[f"tests/test_noise.py::test_{index} PASSED" for index in range(120)],
            "============================== FAILURES ==============================",
            "___ test_something ___",
            "    def test_something():",
            ">       assert False",
            "E       assert False",
            "tests/test_foo.py:10: AssertionError",
            "=========================== short test summary info ===========================",
            "FAILED tests/test_foo.py::test_something - assert False",
            "=================== 159 passed, 1 failed in 12.34s ===================",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 1,
                "command": "pytest -q",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    compaction = payload["_tool_output_budget"]["compaction"]
    assert payload["output_compaction_profile"] == "test"
    assert "mode=structured" in payload["output"]
    assert "Pytest: 159 passed, 1 failed in 12.34s" in payload["output"]
    assert "test_something" in payload["output"]
    assert "assert False" in payload["output"]
    assert "tests/test_noise.py::test_80 PASSED" not in payload["output"]
    assert compaction["profile"] == "test"
    assert compaction["savings"]["chars_saved"] > 0


def test_tool_output_budget_uses_structured_typecheck_filter_for_tsc(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "src/server/api/auth.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.",
            "  Property 'id' is incompatible.",
            "src/server/api/auth.ts(15,10): error TS2345: Argument of type 'number' is not assignable to parameter of type 'string'.",
            "src/components/Button.tsx(8,3): error TS2339: Property 'onClick' does not exist on type 'ButtonProps'.",
            *[f"Found noisy status line {index}" for index in range(120)],
            "Found 3 errors in 2 files.",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 2,
                "command": "npm exec tsc -- --noEmit",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    compaction = payload["_tool_output_budget"]["compaction"]
    assert payload["output_compaction_profile"] == "typecheck"
    assert "mode=structured" in payload["output"]
    assert "TypeScript: 3 issues in 2 files" in payload["output"]
    assert "Top codes:" in payload["output"]
    assert "auth.ts (2 issues)" in payload["output"]
    assert "TS2322" in payload["output"]
    assert "Property 'id' is incompatible" in payload["output"]
    assert "Found noisy status line 80" not in payload["output"]
    assert compaction["profile"] == "typecheck"


def test_tool_output_budget_uses_structured_typecheck_filter_for_mypy(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "src/app.py:10: error: Incompatible types in assignment  [assignment]",
            "src/app.py:10: note: Expected type \"int\"",
            "src/app.py:10: note: Got type \"str\"",
            "src/api.py:20: error: Missing return statement  [return]",
            *[f"checked module {index}" for index in range(120)],
            "Found 2 errors in 2 files (checked 20 source files)",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 1,
                "command": "python -m mypy src",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    assert payload["output_compaction_profile"] == "typecheck"
    assert "mypy: 2 issues in 2 files" in payload["output"]
    assert "assignment" in payload["output"]
    assert "Expected type \"int\"" in payload["output"]
    assert "checked module 80" not in payload["output"]


def test_tool_output_budget_uses_structured_ruff_json_filter(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    diagnostics = [
        {
            "code": "F401",
            "message": "Unused import os",
            "location": {"row": 3, "column": 1},
            "filename": "src/app.py",
            "fix": {"applicability": "safe"},
        },
        {
            "code": "E501",
            "message": "Line too long",
            "location": {"row": 8, "column": 120},
            "filename": "src/app.py",
            "fix": None,
        },
        *[
            {
                "code": "F841",
                "message": f"Unused variable {index}",
                "location": {"row": index + 20, "column": 1},
                "filename": f"src/noise_{index}.py",
                "fix": None,
            }
            for index in range(40)
        ],
    ]
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 1,
                "command": "ruff check --output-format=json .",
                "cwd": "/mnt/user-data/workspace",
                "output": json.dumps(diagnostics),
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    assert payload["output_compaction_profile"] == "lint"
    assert "Ruff: 42 issues in 41 files" in payload["output"]
    assert "(1 fixable)" in payload["output"]
    assert "Top codes:" in payload["output"]
    assert "src/app.py (2 issues)" in payload["output"]
    assert "Unused import os" in payload["output"]
    assert "src/noise_39.py" not in payload["output"]


def test_tool_output_budget_typecheck_profile_falls_back_to_generic_compaction(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    output = "\n".join(
        [
            "Starting compiler service",
            *[f"phase {index}: scanning project graph" for index in range(100)],
            "Compiler service stopped after cache warmup",
        ]
    )
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "completed",
                "exit_code": 0,
                "command": "tsc --watch false",
                "cwd": "/mnt/user-data/workspace",
                "output": output,
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(_request(contract_tmp_path, output_token_budget=24), lambda request: original)

    payload = json.loads(str(result.content))
    assert payload["output_compaction_profile"] == "typecheck"
    assert "profile=typecheck" in payload["output"]
    assert "mode=structured" not in payload["output"]
    assert "phase 80: scanning project graph" not in payload["output"]
    assert payload["_tool_output_budget"]["compaction"]["profile"] == "typecheck"


def test_tool_output_budget_respects_disabled_command_compaction(contract_tmp_path) -> None:
    middleware = ToolOutputBudgetMiddleware()
    request = _request(contract_tmp_path, output_token_budget=24)
    request.runtime.context.config_result.effective_config.tool_output_budget.command_compaction_enabled = False
    original = ToolMessage(
        content=json.dumps(
            {
                "status": "failed",
                "exit_code": 1,
                "command": "pytest -q",
                "output": "\n".join(f"tests/test_noise.py::test_{index} PASSED" for index in range(80)),
            }
        ),
        tool_call_id="call-1",
    )

    result = middleware.wrap_tool_call(request, lambda request: original)

    payload = json.loads(str(result.content))
    assert "compaction" not in payload["_tool_output_budget"]
