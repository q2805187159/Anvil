from __future__ import annotations

from pathlib import Path

from ..config import UploadsConfig
from ..documents import CONVERTIBLE_EXTENSIONS, extract_document
from .file_ops import FileReadResult, slice_text_for_read


def read_textual_file(host_path: Path, *, uploads_config: UploadsConfig | None = None) -> str:
    uploads_config = uploads_config or UploadsConfig()
    if companion_text := _read_markdown_companion(host_path):
        return companion_text
    if host_path.suffix.lower() == ".pdf":
        return _read_pdf_as_text(host_path, uploads_config=uploads_config)
    if host_path.suffix.lower() in CONVERTIBLE_EXTENSIONS:
        try:
            extracted = extract_document(
                host_path,
                prefer_companion=False,
                convert_documents=uploads_config.convert_documents,
                pdf_converter=uploads_config.pdf_converter,
                ocr_enabled=uploads_config.ocr_enabled,
                ocr_strategy=uploads_config.ocr_strategy,
                ocr_languages=uploads_config.ocr_languages,
                max_ocr_pages=uploads_config.max_ocr_pages,
            )
            return extracted.content
        except Exception:
            pass
    return host_path.read_text(encoding="utf-8")


def read_textual_file_window(
    host_path: Path,
    *,
    start_line: int = 1,
    max_lines: int | None = None,
    max_chars: int | None = None,
    uploads_config: UploadsConfig | None = None,
) -> FileReadResult:
    return slice_text_for_read(
        read_textual_file(host_path, uploads_config=uploads_config),
        start_line=start_line,
        max_lines=max_lines,
        max_chars=max_chars,
    )


def _read_markdown_companion(host_path: Path) -> str | None:
    if host_path.suffix.lower() not in CONVERTIBLE_EXTENSIONS:
        return None
    companion_path = host_path.with_suffix(".md")
    if not companion_path.exists():
        return None
    return companion_path.read_text(encoding="utf-8")


def _read_pdf_as_text(host_path: Path, *, uploads_config: UploadsConfig) -> str:
    try:
        extracted = extract_document(
            host_path,
            prefer_companion=True,
            convert_documents=uploads_config.convert_documents,
            pdf_converter=uploads_config.pdf_converter,
            ocr_enabled=uploads_config.ocr_enabled,
            ocr_strategy=uploads_config.ocr_strategy,
            ocr_languages=uploads_config.ocr_languages,
            max_ocr_pages=uploads_config.max_ocr_pages,
        )
        return extracted.content
    except Exception as exc:
        return f"PDF text extraction failed: {exc}"
