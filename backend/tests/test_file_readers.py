from __future__ import annotations

from pathlib import Path

from anvil.sandbox.file_readers import read_textual_file, read_textual_file_window


def _minimal_pdf_bytes(text: str) -> bytes:
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
            b"endobj\n"
        ),
        (
            f"4 0 obj\n<< /Length {len(f'BT\\n/F1 24 Tf\\n72 72 Td\\n({text}) Tj\\nET\\n'.encode('latin-1'))} >>\nstream\n"
            f"BT\n/F1 24 Tf\n72 72 Td\n({text}) Tj\nET\n"
            "endstream\nendobj\n"
        ).encode("latin-1"),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]

    header = b"%PDF-1.4\n"
    chunks = [header]
    offsets = [0]
    current = len(header)
    for obj in objects:
        offsets.append(current)
        chunks.append(obj)
        current += len(obj)

    xref_offset = current
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))

    trailer = (
        b"trailer\n<< /Root 1 0 R /Size 6 >>\n"
        + f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    chunks.extend(xref)
    chunks.append(trailer)
    return b"".join(chunks)


def test_read_textual_file_reads_utf8_text(contract_tmp_path: Path) -> None:
    text_path = contract_tmp_path / "notes.txt"
    text_path.write_text("hello world", encoding="utf-8")

    result = read_textual_file(text_path)

    assert result == "hello world"


def test_read_textual_file_window_returns_line_metadata(contract_tmp_path: Path) -> None:
    text_path = contract_tmp_path / "notes.txt"
    text_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    result = read_textual_file_window(text_path, start_line=2, max_lines=2)

    assert result.content == "two\nthree\n"
    assert result.start_line == 2
    assert result.end_line == 3
    assert result.total_lines == 4
    assert result.truncated is True


def test_read_textual_file_extracts_pdf_text(contract_tmp_path: Path) -> None:
    pdf_path = contract_tmp_path / "resume.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes("Hello PDF Resume"))

    result = read_textual_file(pdf_path)

    assert "Hello PDF Resume" in result


def test_read_textual_file_returns_helpful_message_for_textless_pdf(contract_tmp_path: Path) -> None:
    pdf_path = contract_tmp_path / "scan.pdf"
    pdf_path.write_bytes(_minimal_pdf_bytes(""))

    result = read_textual_file(pdf_path)

    assert "No text could be extracted" in result
