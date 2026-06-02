from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import UploadsConfig
from ..documents import (
    CONVERTIBLE_EXTENSIONS,
    DocumentCompanion,
    DocumentExtractionInfo,
    extract_outline,
    extract_pdf_text as _extract_pdf_text,
    extract_preview,
    ingest_document,
    is_convertible_document,
)


@dataclass(frozen=True)
class DocumentConversionResult:
    extension: str
    markdown_path: Path | None = None
    outline: list[dict[str, Any]] = field(default_factory=list)
    outline_preview: list[str] = field(default_factory=list)
    converter_used: str | None = None
    ocr_used: bool = False
    conversion_error: str | None = None
    companions: tuple[DocumentCompanion, ...] = ()
    extraction: DocumentExtractionInfo | None = None


def convert_document_to_markdown(
    file_path: Path,
    *,
    config: UploadsConfig,
) -> DocumentConversionResult:
    result = ingest_document(
        file_path,
        convert_documents=config.convert_documents,
        pdf_converter=config.pdf_converter,
        ocr_enabled=config.ocr_enabled,
        ocr_strategy=config.ocr_strategy,
        ocr_languages=config.ocr_languages,
        max_ocr_pages=config.max_ocr_pages,
        max_outline_entries=config.max_outline_entries,
        preview_line_count=config.preview_line_count,
    )
    return DocumentConversionResult(
        extension=result.extension,
        markdown_path=result.markdown_path,
        outline=result.outline,
        outline_preview=result.outline_preview,
        converter_used=result.converter_used,
        ocr_used=result.ocr_used,
        conversion_error=result.conversion_error,
        companions=result.companions,
        extraction=result.extraction,
    )


def extract_pdf_text(
    file_path: Path,
    *,
    config: UploadsConfig,
) -> tuple[str | None, str | None, bool, str | None]:
    return _extract_pdf_text(
        file_path,
        pdf_converter=config.pdf_converter,
        ocr_enabled=config.ocr_enabled,
        ocr_strategy=config.ocr_strategy,
        ocr_languages=config.ocr_languages,
        max_ocr_pages=config.max_ocr_pages,
    )
