from __future__ import annotations

import base64
from dataclasses import dataclass, field
import mimetypes
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


IMAGE_UPLOAD_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
IMAGE_UPLOAD_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024


@dataclass(frozen=True)
class ContextEnvelope:
    """Four-way context projection for one model turn.

    Harness may keep LangChain messages in ``model_visible``. App/frontend
    adapters consume only the frontend and persistent projections.
    """

    model_visible: list[Any]
    frontend_visible: dict[str, Any] = field(default_factory=dict)
    persistent_transcript: list[Any] = field(default_factory=list)
    debug_trace: dict[str, Any] = field(default_factory=dict)

    def to_input_payload(
        self,
        *,
        uploaded_files: list[dict[str, Any]],
        title: str | None,
        summary: str | None,
        todos: list[Any],
        token_usage: dict[str, Any],
    ) -> dict[str, object]:
        return {
            "messages": list(self.model_visible),
            "uploaded_files": uploaded_files,
            "title": title,
            "summary": summary,
            "todos": todos,
            "token_usage": token_usage,
            "context_envelope": {
                "frontend_visible": self.frontend_visible,
                "debug_trace": self.debug_trace,
            },
        }


@dataclass(frozen=True)
class ContextContinuationWindow:
    """Policy for selecting the model-visible transcript window for a run."""

    include_current_user_message: bool = True
    drop_pending_assistant_tail: bool = False


class ContextAssembler:
    """Coordinator for conversation-context visibility boundaries."""

    def __init__(self, *, path_service: Any, thread_id: str) -> None:
        self.path_service = path_service
        self.thread_id = thread_id

    def assemble_input_envelope(
        self,
        *,
        history_messages: list[Any],
        translate_message,
        user_message: str,
        include_user_message: bool,
        drop_last_assistant_message: bool,
        uploaded_files: list[dict[str, Any]],
        recent_upload_filenames: tuple[str, ...],
        client_message_id: str | None,
        vision_supported: bool,
    ) -> ContextEnvelope:
        continuation_window = ContextContinuationWindow(
            include_current_user_message=include_user_message,
            drop_pending_assistant_tail=drop_last_assistant_message,
        )
        messages = self.model_visible_history_window(
            history_messages=history_messages,
            translate_message=translate_message,
            continuation_window=continuation_window,
        )

        current_uploads: list[dict[str, Any]] = []
        image_block_count = 0
        if continuation_window.include_current_user_message:
            current_uploads = self.current_turn_uploads(
                uploaded_files=uploaded_files,
                recent_upload_filenames=recent_upload_filenames,
            )
            user_message_content, additional_kwargs, image_block_count = self.current_user_message(
                user_message=user_message,
                current_uploads=current_uploads,
                client_message_id=client_message_id,
                vision_supported=vision_supported,
            )
            messages.append(
                HumanMessage(
                    content=user_message_content,
                    id=f"user-{uuid4().hex[:12]}",
                    additional_kwargs=additional_kwargs,
                )
            )

        return self.build_envelope(
            messages=messages,
            persistent_transcript=self.persistent_transcript(messages),
            current_uploads=current_uploads,
            vision_supported=vision_supported,
            image_block_count=image_block_count,
        )

    def translate_history(self, messages: list[Any], *, translate_message) -> list[Any]:
        return [
            translate_message(message, path_service=self.path_service, thread_id=self.thread_id)
            for message in messages
        ]

    def model_visible_history_window(
        self,
        *,
        history_messages: list[Any],
        translate_message,
        continuation_window: ContextContinuationWindow,
    ) -> list[Any]:
        messages = self.translate_history(history_messages, translate_message=translate_message)
        return self.apply_continuation_window(
            messages=messages,
            continuation_window=continuation_window,
        )

    def apply_continuation_window(
        self,
        *,
        messages: list[Any],
        continuation_window: ContextContinuationWindow,
    ) -> list[Any]:
        if not continuation_window.drop_pending_assistant_tail or not messages:
            return list(messages)
        last_message = messages[-1]
        if getattr(last_message, "type", "") == "ai":
            return list(messages[:-1])
        return list(messages)

    def current_turn_uploads(
        self,
        *,
        uploaded_files: list[dict[str, Any]],
        recent_upload_filenames: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        if not recent_upload_filenames:
            return []
        recent_names = {str(name) for name in recent_upload_filenames if str(name).strip()}
        if not recent_names:
            return []
        current_uploads: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in uploaded_files:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or item.get("label") or "")
            if filename not in recent_names or filename in seen:
                continue
            seen.add(filename)
            current_uploads.append(dict(item))
        return current_uploads

    def current_user_message(
        self,
        *,
        user_message: str,
        current_uploads: list[dict[str, Any]],
        client_message_id: str | None,
        vision_supported: bool,
    ) -> tuple[str | list[dict[str, Any]], dict[str, Any], int]:
        user_message_content: str | list[dict[str, Any]] = self.path_service.translate_user_text_to_runtime(
            user_message,
            thread_id=self.thread_id,
        )
        additional_kwargs: dict[str, Any] = {"display_content": user_message}
        if client_message_id:
            additional_kwargs["client_message_id"] = client_message_id

        image_block_count = 0
        if not current_uploads:
            return user_message_content, additional_kwargs, image_block_count

        additional_kwargs.update(
            {
                "files": current_uploads,
                "uploaded_filenames": [
                    str(item.get("filename")) for item in current_uploads if item.get("filename")
                ],
            }
        )
        user_message_content = self.compose_user_message_with_current_uploads(
            user_message=str(user_message_content),
            uploads=current_uploads,
        )
        image_uploads = [item for item in current_uploads if is_image_upload(item)]
        image_blocks = self.image_content_blocks_for_uploads(uploads=image_uploads)
        image_block_count = sum(1 for block in image_blocks if block.get("type") == "image_url")
        if image_blocks and vision_supported:
            return [
                {"type": "text", "text": user_message_content},
                *image_blocks,
            ], additional_kwargs, image_block_count
        if image_uploads and not vision_supported:
            user_message_content = (
                f"{user_message_content}\n\n"
                "<image_attachments_unavailable>\n"
                "Image files are attached to this turn, but the selected model route does not support vision. "
                "Do not claim to have inspected the pixels unless a vision-capable route or tool is available.\n"
                "</image_attachments_unavailable>"
            )
        return user_message_content, additional_kwargs, image_block_count

    def compose_user_message_with_current_uploads(
        self,
        *,
        user_message: str,
        uploads: list[dict[str, Any]],
    ) -> str:
        lines = [
            "<attached_files>",
            "Files uploaded with this user message. Treat these as the files referenced by this turn:",
        ]
        for item in uploads:
            filename = str(item.get("filename") or item.get("label") or "upload")
            virtual_path = str(item.get("virtual_path") or "")
            lines.append(f"- {filename}: {virtual_path}")
            markdown_virtual_path = str(item.get("markdown_virtual_path") or "")
            if markdown_virtual_path:
                lines.append(f"  - Analysis companion: {markdown_virtual_path}")
            companions = item.get("companions") if isinstance(item.get("companions"), list) else []
            for companion in companions:
                if not isinstance(companion, dict) or companion.get("internal"):
                    continue
                if companion.get("kind") == "markdown":
                    continue
                companion_label = str(companion.get("label") or companion.get("kind") or "companion")
                companion_path = str(companion.get("virtual_path") or "")
                if companion_path:
                    lines.append(f"  - Companion ({companion_label}): {companion_path}")
            extension = str(item.get("extension") or "")
            if extension == ".pdf":
                lines.append("  - PDF document: prefer the analysis companion first; use the raw PDF only as fallback.")
        lines.extend(
            [
                "Use read_file or extract_document on these paths before acting on file contents.",
                "</attached_files>",
                "",
                user_message,
            ]
        )
        return "\n".join(lines)

    def image_content_blocks_for_uploads(self, *, uploads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for item in uploads:
            data_url = self.image_data_url_for_upload(item)
            if data_url is None:
                continue
            filename = _single_line(str(item.get("filename") or item.get("label") or "upload"))
            virtual_path = _single_line(str(item.get("virtual_path") or ""))
            blocks.extend(
                [
                    {
                        "type": "text",
                        "text": f"<image_attachment>\nfilename: {filename}\npath: {virtual_path}",
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "</image_attachment>"},
                ]
            )
        return blocks

    def image_data_url_for_upload(self, item: dict[str, Any]) -> str | None:
        virtual_path = str(item.get("virtual_path") or "")
        if not virtual_path:
            return None
        mime_type = image_mime_type_for_upload(item)
        if mime_type is None:
            return None
        try:
            host_path = self.path_service.resolve_virtual_path(self.thread_id, virtual_path)
        except Exception:
            return None
        if not host_path.exists() or not host_path.is_file():
            return None
        try:
            if host_path.stat().st_size > MAX_INLINE_IMAGE_BYTES:
                return None
            encoded = base64.b64encode(host_path.read_bytes()).decode("ascii")
        except Exception:
            return None
        return f"data:{mime_type};base64,{encoded}"

    def persistent_transcript(self, messages: list[Any]) -> list[Any]:
        stored = []
        for message in messages:
            additional_kwargs = dict(getattr(message, "additional_kwargs", {}) or {})
            if additional_kwargs.get("anvil_model_only") or additional_kwargs.get("anvil_view_image_injection"):
                continue
            if not isinstance(message, HumanMessage):
                stored.append(message)
                continue
            display_source = additional_kwargs.get("display_content")
            if isinstance(display_source, (str, list)):
                additional_kwargs["display_content"] = self.display_content_for_human_storage(display_source)
            else:
                additional_kwargs["display_content"] = self.display_content_for_human_storage(
                    getattr(message, "content", "")
                )
            stored.append(message.model_copy(update={"additional_kwargs": additional_kwargs}))
        return stored

    def display_content_for_human_storage(self, content: object) -> object:
        if isinstance(content, list):
            return self.path_service.translate_runtime_data_to_display(content, thread_id=self.thread_id)
        if not isinstance(content, str):
            return content
        display = content
        end_marker = "</attached_files>"
        if end_marker in display:
            display = display.split(end_marker, 1)[1].lstrip()
        return self.path_service.translate_runtime_text_to_display(display, thread_id=self.thread_id) or display

    def compacted_summary_model_call(
        self,
        *,
        messages: list[Any],
        system_prompt: str | None,
        summary_context: str | None,
        keep_recent_turns: int,
    ) -> tuple[list[Any], SystemMessage | None]:
        summary_text = str(summary_context or "").strip()
        if not summary_text:
            return list(messages), None
        compacted_messages = self.recent_legal_transcript_window(
            messages=list(messages),
            keep_recent_turns=keep_recent_turns,
        )
        system_prompt_with_summary = self.system_prompt_with_summary(
            system_prompt=system_prompt,
            summary_context=summary_text,
        )
        return compacted_messages, SystemMessage(content=system_prompt_with_summary)

    def recent_legal_transcript_window(self, *, messages: list[Any], keep_recent_turns: int) -> list[Any]:
        if keep_recent_turns < 0 or len(messages) <= keep_recent_turns:
            return list(messages)

        start = max(len(messages) - keep_recent_turns, 0)
        selected_indexes = set(range(start, len(messages)))
        assistant_by_tool_call_id: dict[str, int] = {}
        tool_result_indexes_by_call_id: dict[str, list[int]] = {}
        for index, message in enumerate(messages):
            if isinstance(message, AIMessage):
                for tool_call in getattr(message, "tool_calls", None) or []:
                    if not isinstance(tool_call, dict):
                        continue
                    tool_call_id = str(tool_call.get("id") or "")
                    if tool_call_id:
                        assistant_by_tool_call_id[tool_call_id] = index
            elif isinstance(message, ToolMessage):
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                if tool_call_id:
                    tool_result_indexes_by_call_id.setdefault(tool_call_id, []).append(index)

        changed = True
        while changed:
            changed = False
            for index in list(selected_indexes):
                message = messages[index]
                if isinstance(message, ToolMessage):
                    tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                    assistant_index = assistant_by_tool_call_id.get(tool_call_id)
                    if assistant_index is not None and assistant_index not in selected_indexes:
                        selected_indexes.add(assistant_index)
                        changed = True
                elif isinstance(message, AIMessage):
                    for tool_call in getattr(message, "tool_calls", None) or []:
                        if not isinstance(tool_call, dict):
                            continue
                        tool_call_id = str(tool_call.get("id") or "")
                        if not tool_call_id:
                            continue
                        for tool_result_index in tool_result_indexes_by_call_id.get(tool_call_id, ()):
                            if tool_result_index not in selected_indexes:
                                selected_indexes.add(tool_result_index)
                                changed = True

        legal_indexes = []
        for index in sorted(selected_indexes):
            message = messages[index]
            if isinstance(message, ToolMessage):
                tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                if tool_call_id and assistant_by_tool_call_id.get(tool_call_id) not in selected_indexes:
                    continue
            legal_indexes.append(index)
        return [messages[index] for index in legal_indexes]

    def system_prompt_with_summary(self, *, system_prompt: str | None, summary_context: str) -> str:
        summary_block = f"<conversation_summary>\n{summary_context}\n</conversation_summary>"
        current_prompt = system_prompt or ""
        if summary_block in current_prompt:
            return current_prompt
        return f"{current_prompt}\n\n{summary_block}" if current_prompt else summary_block

    def build_envelope(
        self,
        *,
        messages: list[Any],
        persistent_transcript: list[Any],
        current_uploads: list[dict[str, Any]],
        vision_supported: bool,
        image_block_count: int,
    ) -> ContextEnvelope:
        image_upload_count = sum(1 for item in current_uploads if _looks_like_image_upload(item))
        return ContextEnvelope(
            model_visible=messages,
            persistent_transcript=persistent_transcript,
            frontend_visible={
                "current_turn_upload_count": len(current_uploads),
                "current_turn_image_count": image_upload_count,
                "vision_supported": vision_supported,
            },
            debug_trace={
                "model_message_count": len(messages),
                "persistent_message_count": len(persistent_transcript),
                "current_turn_upload_count": len(current_uploads),
                "current_turn_image_count": image_upload_count,
                "model_image_block_count": image_block_count,
                "unsupported_image_placeholder": bool(image_upload_count and not vision_supported),
            },
        )


def _looks_like_image_upload(item: dict[str, Any]) -> bool:
    mime_type = str(item.get("mime_type") or item.get("content_type") or "").lower()
    if mime_type.startswith("image/"):
        return True
    extension = str(item.get("extension") or "").lower()
    if extension in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return True
    filename = str(item.get("filename") or item.get("label") or "").lower()
    return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif"))


def is_image_upload(item: dict[str, Any]) -> bool:
    return image_mime_type_for_upload(item) in IMAGE_UPLOAD_MIME_TYPES


def image_mime_type_for_upload(item: dict[str, Any]) -> str | None:
    explicit_mime_type = str(item.get("mime_type") or item.get("media_type") or "").strip().lower()
    if explicit_mime_type in IMAGE_UPLOAD_MIME_TYPES:
        return explicit_mime_type
    extension = str(item.get("extension") or "").strip().lower()
    filename = str(item.get("filename") or item.get("label") or item.get("virtual_path") or "")
    if not extension:
        extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension in IMAGE_UPLOAD_EXTENSIONS:
        guessed = mimetypes.guess_type(filename or f"upload{extension}")[0]
        return guessed if guessed in IMAGE_UPLOAD_MIME_TYPES else _mime_type_for_image_extension(extension)
    return None


def _mime_type_for_image_extension(extension: str) -> str | None:
    normalized = extension.lower()
    if normalized in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if normalized == ".png":
        return "image/png"
    if normalized == ".webp":
        return "image/webp"
    if normalized == ".gif":
        return "image/gif"
    return None


def _single_line(value: str) -> str:
    return value.replace("\r", " ").replace("\n", " ").strip()
