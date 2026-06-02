from __future__ import annotations

from anvil.agents.middlewares.tool_error_middleware import ToolErrorHandlingMiddleware


def test_patch_file_errors_include_patch_guidance() -> None:
    middleware = ToolErrorHandlingMiddleware()

    message = middleware._build_error_message(
        tool_name="patch_file",
        error=ValueError("anchor not found"),
    )

    assert "anchor not found" in message
    assert "retry patch_file with one exact anchor" in message


def test_patch_file_path_errors_include_path_guidance() -> None:
    middleware = ToolErrorHandlingMiddleware()

    message = middleware._build_error_message(
        tool_name="patch_file",
        error=ValueError("patch target does not exist: /tmp/demo.txt"),
    )

    assert "/mnt/user-data/workspace" in message
    assert "/mnt/user-data/workspace/_host/<alias>" in message
    assert "existing UTF-8 text files" in message
