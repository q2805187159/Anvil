from __future__ import annotations

from pathlib import Path


def test_upload_returns_virtual_path_and_artifact_descriptor(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-upload"})
    response = gateway_client.post(
        "/threads/thread-upload/uploads",
        files=[("files", ("note.txt", b"hello world", "text/plain"))],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["thread_id"] == "thread-upload"
    assert payload["files"][0]["filename"] == "note.txt"
    assert payload["files"][0]["virtual_path"] == "/mnt/user-data/uploads/note.txt"
    assert payload["files"][0]["artifact_url"].startswith("/threads/thread-upload/artifacts/uploads/")
    assert payload["files"][0]["artifact_url"].endswith("/thread-upload/artifacts/uploads/note.txt")


def test_upload_returns_markdown_companion_metadata_when_conversion_succeeds(gateway_client, monkeypatch) -> None:
    from anvil.uploads import service as upload_service_module
    from anvil.uploads.conversion import DocumentConversionResult

    def fake_convert_document_to_markdown(file_path: Path, *, config):
        markdown_path = file_path.with_suffix(".md")
        markdown_path.write_text("# Converted document\n\nSummary", encoding="utf-8")
        return DocumentConversionResult(
            extension=".pdf",
            markdown_path=markdown_path,
            outline=[{"title": "Converted document", "line": 1}],
            outline_preview=[],
            converter_used="test-converter",
            ocr_used=False,
            conversion_error=None,
        )

    monkeypatch.setattr(upload_service_module, "convert_document_to_markdown", fake_convert_document_to_markdown)

    gateway_client.post("/threads", json={"thread_id": "thread-upload-pdf"})
    response = gateway_client.post(
        "/threads/thread-upload-pdf/uploads",
        files=[("files", ("resume.pdf", b"%PDF-1.4 fake", "application/pdf"))],
    )

    assert response.status_code == 200
    payload = response.json()
    file_payload = payload["files"][0]
    assert file_payload["filename"] == "resume.pdf"
    assert file_payload["markdown_file"] == "resume.md"
    assert file_payload["markdown_artifact_url"].endswith("/threads/thread-upload-pdf/artifacts/uploads/resume.md")
    assert file_payload["outline"][0]["title"] == "Converted document"
    assert file_payload["converter_used"] == "test-converter"


def test_list_uploads_and_get_artifact(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-files"})
    gateway_client.post(
        "/threads/thread-files/uploads",
        files=[("files", ("report.md", b"# report", "text/markdown"))],
    )

    listed = gateway_client.get("/threads/thread-files/uploads")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["filename"] == "report.md"

    artifact = gateway_client.get("/threads/thread-files/artifacts/uploads/report.md")
    assert artifact.status_code == 200
    assert artifact.content == b"# report"


def test_artifact_traversal_is_rejected(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-safe"})
    response = gateway_client.get("/threads/thread-safe/artifacts/uploads/..%2Fsecret.txt")
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_artifact_path"


def test_artifact_backslash_traversal_is_rejected(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-safe-backslash"})
    response = gateway_client.get("/threads/thread-safe-backslash/artifacts/uploads/..%5Csecret.txt")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_artifact_path"
    assert "secret.txt" not in (payload.get("detail") or "")


def test_artifact_windows_absolute_path_is_rejected_without_echoing_path(gateway_client) -> None:
    gateway_client.post("/threads", json={"thread_id": "thread-safe-absolute"})
    response = gateway_client.get(
        "/threads/thread-safe-absolute/artifacts/uploads/C:%5CUsers%5Ctester%5Csecret.txt"
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"] == "invalid_artifact_path"
    assert "C:" not in (payload.get("detail") or "")
    assert "secret.txt" not in (payload.get("detail") or "")
