"""Safety and stability layer.

Reads: tool_call, tracing_service
Writes: tool result messages and tracing events
Side effects: tracing emission
Failure behavior: converts tool exceptions into recoverable ToolMessage payloads
"""

from __future__ import annotations

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


FILESYSTEM_TOOL_NAMES = {"list_dir", "read_file", "write_file", "patch_file", "search_files", "glob_files", "grep_files"}
FILESYSTEM_PATH_GUIDANCE = (
    "Use /mnt/user-data for discovery with list_dir. "
    "Use read_file with /mnt/user-data/workspace, /mnt/user-data/uploads, /mnt/user-data/outputs, or a configured /mnt/user-data/workspace/_host/<alias> bridge. "
    "Use write_file only with /mnt/user-data/workspace, /mnt/user-data/outputs, or a configured bridge. "
    "Use patch_file only for existing UTF-8 text files under /mnt/user-data/workspace, /mnt/user-data/outputs, or a configured bridge. "
    "Do not use '.', '/', or unlisted host paths."
)
FILESYSTEM_PATCH_GUIDANCE = (
    "Read the file first, then retry patch_file with one exact anchor, exact old text, or a valid line range. "
    "If you intend to replace the whole file, use write_file instead."
)


class ToolErrorHandlingMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def wrap_tool_call(self, request, handler):
        tracing_service = request.runtime.context.tracing_service
        run_trace_id = request.runtime.context.run_trace_id
        tool_name = request.tool_call["name"]
        tool_call_id = request.tool_call.get("id")
        if tracing_service is not None and run_trace_id is not None:
            tracing_service.tool_started(
                trace_id=run_trace_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
            )
        try:
            response = handler(request)
            if tracing_service is not None and run_trace_id is not None:
                tracing_service.tool_finished(
                    trace_id=run_trace_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status="completed",
                )
            return response
        except Exception as exc:  # noqa: BLE001
            if tracing_service is not None and run_trace_id is not None:
                tracing_service.tool_finished(
                    trace_id=run_trace_id,
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    status="error",
                    error=str(exc),
                )
            return ToolMessage(
                content=self._build_error_message(
                    tool_name=tool_name,
                    error=exc,
                ),
                tool_call_id=request.tool_call["id"],
                status="error",
            )

    def _build_error_message(self, *, tool_name: str, error: Exception) -> str:
        detail = str(error)
        if tool_name not in FILESYSTEM_TOOL_NAMES:
            return f"Tool execution failed: {detail}"

        if any(
            marker in detail
            for marker in (
                "unsupported virtual path prefix",
                "directory discovery only",
                "path escapes allowed root",
                "path is not a directory",
                "patch target does not exist",
                "patch target is not a file",
            )
        ):
            return f"Tool execution failed: {detail}. {FILESYSTEM_PATH_GUIDANCE}"

        if any(
            marker in detail
            for marker in (
                "patch operations must not be empty",
                "unsupported patch action",
                "invalid patch operation",
                "anchor not found",
                "anchor matched multiple locations",
                "text not found",
                "text matched multiple locations",
                "line number out of bounds",
                "line range out of bounds",
                "expected old text mismatch",
            )
        ):
            return f"Tool execution failed: {detail}. {FILESYSTEM_PATCH_GUIDANCE}"

        return f"Tool execution failed: {detail}"


ToolErrorMiddleware = ToolErrorHandlingMiddleware
