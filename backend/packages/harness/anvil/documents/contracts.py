from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DOCUMENT_ANALYSIS_SCOPE = "analysis"
DOCUMENT_OUTPUT_SCOPE = "output"
DOCUMENT_SCRATCH_SCOPE = "scratch"
DOCUMENT_UPLOAD_SCOPE = "upload"

MARKDOWN_COMPANION_KIND = "markdown"


@dataclass(frozen=True)
class DocumentCompanion:
    kind: str
    label: str
    path: Path
    provider: str | None = None
    internal: bool = False
    source_scope: str = DOCUMENT_ANALYSIS_SCOPE


@dataclass(frozen=True)
class DocumentExtractionInfo:
    status: str = "skipped"
    provider: str | None = None
    ocr_provider: str | None = None
    page_count: int | None = None
    text_layer_present: bool | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentIngestionResult:
    extension: str
    markdown_path: Path | None = None
    companions: tuple[DocumentCompanion, ...] = ()
    extraction: DocumentExtractionInfo | None = None
    outline: list[dict[str, Any]] = field(default_factory=list)
    outline_preview: list[str] = field(default_factory=list)
    conversion_error: str | None = None

    @property
    def converter_used(self) -> str | None:
        return self.extraction.provider if self.extraction is not None else None

    @property
    def ocr_used(self) -> bool:
        return bool(self.extraction and self.extraction.ocr_provider)


@dataclass(frozen=True)
class ExtractedDocumentResult:
    source_path: Path
    content_path: Path
    content: str
    extraction: DocumentExtractionInfo | None = None
    companions: tuple[DocumentCompanion, ...] = ()
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportedDocumentResult:
    output_path: Path
    mode: str
    format: str
    provider: str
    warnings: tuple[str, ...] = ()
    scratch_paths: tuple[Path, ...] = ()
    cleaned_scratch_paths: tuple[Path, ...] = ()
    preflight: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

