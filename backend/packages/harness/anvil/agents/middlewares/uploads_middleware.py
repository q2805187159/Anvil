from __future__ import annotations

from pathlib import Path

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from anvil.agents.lead_agent.types import LeadAgentContext, LeadAgentState


class UploadsMiddleware(AgentMiddleware[LeadAgentState, LeadAgentContext]):
    state_schema = LeadAgentState

    def before_agent(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        uploaded_files = state_obj.uploaded_files or list(runtime.context.initial_uploaded_files or ())
        if not uploaded_files:
            return None
        return {"uploaded_files": uploaded_files}

    def before_model(self, state: LeadAgentState, runtime):
        state_obj = state if isinstance(state, LeadAgentState) else LeadAgentState.model_validate(state)
        if runtime.context.upload_context or not state_obj.uploaded_files:
            return None
        runtime.context.upload_context = self._render_upload_context(
            state_obj.uploaded_files,
            recent_upload_filenames=set(runtime.context.recent_upload_filenames or ()),
        )
        return {"upload_context": runtime.context.upload_context}

    def wrap_model_call(self, request, handler):
        upload_context = request.runtime.context.upload_context
        if not upload_context:
            return handler(request)
        system_prompt = request.system_prompt or ""
        if "<upload_context>" in system_prompt:
            return handler(request)
        updated_prompt = f"{system_prompt}\n\n{upload_context}" if system_prompt else upload_context
        return handler(request.override(system_message=SystemMessage(content=updated_prompt)))

    def _render_upload_context(
        self,
        uploaded_files: list[dict[str, object]],
        *,
        recent_upload_filenames: set[str],
    ) -> str:
        new_items = []
        historical_items = []
        for item in uploaded_files:
            filename = str(item.get("filename") or item.get("label") or "upload")
            if filename in recent_upload_filenames:
                new_items.append(item)
            else:
                historical_items.append(item)

        rendered = []
        if new_items:
            rendered.append("New files uploaded for this request:")
            for item in new_items:
                rendered.extend(self._render_upload_item(item))
        if historical_items:
            if rendered:
                rendered.append("")
            rendered.append("Previously uploaded files still available:")
            for item in historical_items:
                rendered.extend(self._render_upload_item(item))
        if not rendered:
            return ""
        rendered.extend(
            [
                "",
                "Guidance:",
                "- Prefer extract_document or read_file on the analysis companion when present.",
                "- Use export_document for final .docx delivery instead of hand-building archives through shell commands.",
                "- Use run_command only when extract_document/export_document cannot satisfy the task.",
            ]
        )
        return "<upload_context>\n" + "\n".join(rendered) + "\n</upload_context>"

    def _render_upload_item(self, item: dict[str, object]) -> list[str]:
        rendered: list[str] = []
        filename = str(item.get("filename") or item.get("label") or "upload")
        virtual_path = str(item.get("virtual_path") or "")
        extension = str(item.get("extension") or Path(filename).suffix.lower())
        markdown_virtual_path = str(item.get("markdown_virtual_path") or "")
        converter_used = str(item.get("converter_used") or "")
        ocr_used = bool(item.get("ocr_used", False))
        conversion_error = str(item.get("conversion_error") or "") if item.get("conversion_error") else None
        outline = item.get("outline") if isinstance(item.get("outline"), list) else []
        preview = item.get("outline_preview") if isinstance(item.get("outline_preview"), list) else []
        companions = item.get("companions") if isinstance(item.get("companions"), list) else []
        extraction = item.get("extraction") if isinstance(item.get("extraction"), dict) else {}

        rendered.append(f"- {filename}: {virtual_path}")
        if markdown_virtual_path:
            rendered.append(f"  - Analysis companion: {markdown_virtual_path}")
        for companion in companions:
            if not isinstance(companion, dict):
                continue
            if companion.get("internal"):
                continue
            if companion.get("kind") == "markdown":
                continue
            companion_label = str(companion.get("label") or companion.get("kind") or "companion")
            companion_path = str(companion.get("virtual_path") or "")
            rendered.append(f"  - Companion ({companion_label}): {companion_path}")
        if extension == ".pdf":
            rendered.append("  - PDF document: prefer the analysis companion first; use the raw PDF only as fallback.")
        if converter_used:
            rendered.append(f"  - Provider: {converter_used}{' (OCR)' if ocr_used else ''}")
        if extraction:
            diagnostics = extraction.get("diagnostics")
            if isinstance(diagnostics, list) and diagnostics:
                rendered.append("  - Diagnostics:")
                for diagnostic in diagnostics[:3]:
                    rendered.append(f"    - {diagnostic}")
        if outline:
            rendered.append("  - Document outline:")
            for entry in outline:
                if not isinstance(entry, dict):
                    continue
                if entry.get("truncated"):
                    rendered.append("    - ... additional headings omitted")
                    continue
                title = str(entry.get("title") or "").strip()
                line = entry.get("line")
                if title:
                    rendered.append(f"    - L{line}: {title}" if line is not None else f"    - {title}")
        elif preview:
            rendered.append("  - Preview:")
            for line in preview:
                rendered.append(f"    - {line}")
        if conversion_error:
            rendered.append(f"  - Conversion note: {conversion_error}")
        return rendered
