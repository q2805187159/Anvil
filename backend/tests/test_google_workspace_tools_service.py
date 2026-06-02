from __future__ import annotations

import json

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.google_workspace import GoogleWorkspaceService
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider


def _config(google_workspace: dict[str, object]):
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                        }
                    },
                    "google_workspace": google_workspace,
                },
            )
        ]
    )


def _mock_config():
    return _config(
        {
            "provider": "mock",
            "mock_gmail_messages": [
                {
                    "id": "msg_1",
                    "thread_id": "thread_1",
                    "subject": "Anvil status",
                    "from": "lead@example.com",
                    "to": "agent@example.com",
                    "date": "2026-05-10T08:00:00Z",
                    "snippet": "Browser toolset shipped",
                    "body": "Browser toolset shipped without leaking sk-testsecretsecretsecret.",
                    "label_ids": ["INBOX", "IMPORTANT"],
                }
            ],
            "mock_calendar_events": [
                {
                    "id": "event_1",
                    "calendar_id": "primary",
                    "summary": "Anvil review",
                    "start": {"dateTime": "2026-05-10T10:00:00Z"},
                    "end": {"dateTime": "2026-05-10T11:00:00Z"},
                    "attendees": [{"email": "agent@example.com"}],
                }
            ],
        }
    )


def test_google_workspace_mock_contracts_without_network() -> None:
    config_result = _mock_config()
    service = GoogleWorkspaceService()

    search = service.gmail_search(config_result=config_result, query="status", max_results=5)
    read = service.gmail_read(config_result=config_result, message_id="msg_1")
    labels = service.gmail_labels(config_result=config_result)
    draft = service.gmail_create_draft(config_result=config_result, to="user@example.com", subject="Draft", body="hello")
    sent = service.gmail_send(config_result=config_result, to="user@example.com", subject="Sent", body="hello")
    listed = service.calendar_list_events(config_result=config_result, time_min="2026-05-10T00:00:00Z", time_max="2026-05-11T00:00:00Z")
    free_busy = service.calendar_free_busy(config_result=config_result, time_min="2026-05-10T00:00:00Z", time_max="2026-05-11T00:00:00Z")
    created = service.calendar_create_event(config_result=config_result, summary="New event", start="2026-05-10T12:00:00Z", end="2026-05-10T13:00:00Z")
    updated = service.calendar_update_event(config_result=config_result, event_id="event_1", summary="Updated review")
    deleted = service.calendar_delete_event(config_result=config_result, event_id="event_1")

    assert search["success"] is True
    assert search["messages"][0]["id"] == "msg_1"
    assert read["success"] is True
    assert "[REDACTED]" in read["message"]["body"]
    assert labels["success"] is True
    assert any(item["id"] == "INBOX" for item in labels["labels"])
    assert draft["draft"]["id"] == "draft_1"
    assert sent["message"]["id"] == "sent_1"
    assert listed["events"][0]["summary"] == "Anvil review"
    assert free_busy["calendars"]["primary"]["busy"][0]["start"] == "2026-05-10T10:00:00Z"
    assert created["event"]["summary"] == "New event"
    assert updated["event"]["summary"] == "Updated review"
    assert deleted["success"] is True


def test_runtime_google_workspace_handlers_are_visible(contract_tmp_path) -> None:
    config_result = _mock_config()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-google-workspace", path_service=path_service)

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    expected = {
        "gmail_search",
        "gmail_read",
        "gmail_labels",
        "gmail_send",
        "gmail_create_draft",
        "calendar_list_events",
        "calendar_create_event",
        "calendar_update_event",
        "calendar_delete_event",
        "calendar_free_busy",
    }
    assert expected.issubset(handlers)
    search = json.loads(handlers["gmail_search"].invoke({"query": "status"}))
    event = json.loads(handlers["calendar_create_event"].invoke({"summary": "Demo", "start": "2026-05-10T12:00:00Z", "end": "2026-05-10T13:00:00Z"}))

    assert search["success"] is True
    assert search["messages"][0]["subject"] == "Anvil status"
    assert event["success"] is True
    assert event["event"]["summary"] == "Demo"


def test_google_workspace_errors_scrub_access_tokens(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "ya29.testsecretsecretsecretsecret")
    config_result = _config({"provider": "google", "access_token": "$GOOGLE_ACCESS_TOKEN"})

    def fake_request_json(*args, **kwargs):
        raise RuntimeError("upstream rejected ya29.testsecretsecretsecretsecret")

    monkeypatch.setattr("anvil.google_workspace.service._request_json", fake_request_json)
    payload = GoogleWorkspaceService().gmail_search(config_result=config_result, query="status")

    assert payload["success"] is False
    assert "[REDACTED]" in payload["error"]
    assert "ya29.testsecret" not in payload["error"]
