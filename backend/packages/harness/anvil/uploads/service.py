from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agents import ThreadMetadataView, ThreadState
from ..config import UploadsConfig
from ..documents import DOCUMENT_UPLOAD_SCOPE, DocumentCompanion, DocumentIngestionResult
from ..sandbox.path_service import ArtifactDescriptor, ArtifactKind, PathService
from .conversion import convert_document_to_markdown


class UploadServiceError(Exception):
    """Base upload service error."""


class UploadThreadNotFoundError(UploadServiceError):
    """Thread does not exist."""


class UploadValidationError(UploadServiceError):
    """Upload input or artifact path is invalid."""


class UploadArtifactNotFoundError(UploadServiceError):
    """Artifact bytes were requested but no file exists."""


@dataclass(frozen=True)
class UploadWriteResult:
    filename: str
    descriptor: ArtifactDescriptor
    payload: dict[str, Any]


class UploadService:
    def __init__(self, *, path_service: PathService, checkpointer, store, uploads_config: UploadsConfig | None = None) -> None:
        self.path_service = path_service
        self.checkpointer = checkpointer
        self.store = store
        self.uploads_config = uploads_config or UploadsConfig()

    def write_files(self, thread_id: str, files: list[tuple[str, bytes]]) -> list[UploadWriteResult]:
        state = self._require_thread_state(thread_id)
        upload_root = Path(
            state.thread_data.uploads_path or self.path_service.bootstrap_thread_paths(thread_id).uploads_path
        )

        uploaded_files = [
            payload for payload in state.artifacts.uploaded_files
            if not isinstance(payload, dict) or payload.get("filename") not in {Path(name).name for name, _ in files}
        ]
        results: list[UploadWriteResult] = []
        for raw_name, content in files:
            safe_name = Path(raw_name).name
            if not safe_name or safe_name != raw_name:
                raise UploadValidationError(f"invalid upload filename '{raw_name}'")

            target = upload_root / safe_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

            descriptor = self.path_service.to_artifact_descriptor(thread_id, ArtifactKind.UPLOADS, safe_name)
            payload: dict[str, Any] = {
                "filename": safe_name,
                "virtual_path": descriptor.virtual_path,
                "artifact_url": descriptor.artifact_url,
                "extension": target.suffix.lower(),
                "source_scope": DOCUMENT_UPLOAD_SCOPE,
            }

            conversion_result = convert_document_to_markdown(target, config=self.uploads_config)
            payload.update(self._document_payload(thread_id=thread_id, result=conversion_result))

            uploaded_files.append(payload)
            results.append(UploadWriteResult(filename=safe_name, descriptor=descriptor, payload=payload))

        state.artifacts.uploaded_files = uploaded_files
        self._persist_thread_state(state)
        return results

    def list_uploaded_files(self, thread_id: str) -> list[dict[str, Any]]:
        state = self._require_thread_state(thread_id)
        return list(state.artifacts.uploaded_files)

    def read_artifact(
        self,
        thread_id: str,
        kind: str,
        relative_path: str,
    ) -> tuple[ArtifactDescriptor, bytes, str]:
        self._require_thread_state(thread_id)
        try:
            descriptor = self.path_service.to_artifact_descriptor(thread_id, kind, relative_path)
            host_path = self.path_service.resolve_virtual_path(thread_id, descriptor.virtual_path)
        except Exception as exc:  # noqa: BLE001
            raise UploadValidationError(str(exc)) from exc

        if not host_path.exists() or not host_path.is_file():
            raise UploadArtifactNotFoundError(f"artifact '{relative_path}' was not found")

        media_type = mimetypes.guess_type(host_path.name)[0] or "application/octet-stream"
        return descriptor, host_path.read_bytes(), media_type

    def _require_thread_state(self, thread_id: str) -> ThreadState:
        state = self.checkpointer.get_thread_state(thread_id)
        if state is None:
            raise UploadThreadNotFoundError(f"thread '{thread_id}' was not found")
        return state

    def _persist_thread_state(self, state: ThreadState) -> None:
        self.checkpointer.put_thread_state(state)
        self.store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))

    def _document_payload(
        self,
        *,
        thread_id: str,
        result: DocumentIngestionResult,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "outline": result.outline or [],
            "outline_preview": result.outline_preview or [],
            "converter_used": result.converter_used,
            "ocr_used": result.ocr_used,
            "conversion_error": result.conversion_error,
            "companions": [],
            "extraction": None,
        }
        extraction = result.extraction
        if extraction is None and (result.converter_used or result.ocr_used):
            extraction = {
                "status": "completed" if not result.conversion_error else "failed",
                "provider": result.converter_used,
                "ocr_provider": result.converter_used if result.ocr_used else None,
                "page_count": None,
                "text_layer_present": None,
                "diagnostics": [result.conversion_error] if result.conversion_error else [],
            }
        if extraction is not None and hasattr(extraction, "status"):
            payload["extraction"] = {
                "status": extraction.status,
                "provider": extraction.provider,
                "ocr_provider": extraction.ocr_provider,
                "page_count": extraction.page_count,
                "text_layer_present": extraction.text_layer_present,
                "diagnostics": list(extraction.diagnostics),
            }
        elif isinstance(extraction, dict):
            payload["extraction"] = dict(extraction)

        companions: list[dict[str, Any]] = []
        for companion in result.companions:
            companions.append(self._companion_payload(thread_id=thread_id, companion=companion))
        if not companions and result.markdown_path is not None:
            companions.append(
                self._companion_payload(
                    thread_id=thread_id,
                    companion=DocumentCompanion(
                        kind="markdown",
                        label=result.markdown_path.name,
                        path=result.markdown_path,
                        provider=result.converter_used,
                    ),
                )
            )
        payload["companions"] = companions

        markdown = next((item for item in companions if item.get("kind") == "markdown"), None)
        if markdown is not None:
            payload.update(
                {
                    "markdown_file": markdown["label"],
                    "markdown_virtual_path": markdown["virtual_path"],
                    "markdown_artifact_url": markdown["artifact_url"],
                }
            )
        return payload

    def _companion_payload(
        self,
        *,
        thread_id: str,
        companion: DocumentCompanion,
    ) -> dict[str, Any]:
        descriptor = self.path_service.to_artifact_descriptor(
            thread_id,
            ArtifactKind.UPLOADS,
            companion.path.name,
        )
        return {
            "kind": companion.kind,
            "label": companion.label,
            "artifact_url": descriptor.artifact_url,
            "virtual_path": descriptor.virtual_path,
            "provider": companion.provider,
            "internal": companion.internal,
            "source_scope": companion.source_scope,
        }
