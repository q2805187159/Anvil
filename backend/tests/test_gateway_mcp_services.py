from __future__ import annotations

import asyncio
import json

import pytest
import yaml

from anvil.config.loader import build_default_config_layers
from app.contracts import McpServerBatchUpsertRequest
from app.gateway import services
from app.gateway.deps import build_app_runtime_deps
from app.gateway.services import GatewayAdapterError
from conftest import build_gateway_config_layers


def drain_system_events(queue) -> list:
    events = []
    while True:
        try:
            events.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            return events


def assert_mcp_reload_events_match_response(events: list, payload: dict) -> None:
    event_names = [event.event for event in events]
    assert "config_reloaded" in event_names
    assert "skills_changed" in event_names
    assert "capabilities_changed" in event_names
    capabilities_event = next(
        event for event in events if event.event == "capabilities_changed"
    )
    assert (
        capabilities_event.data["mcp_servers_connected"]
        == payload["reload"]["mcp_servers_connected"]
    )


def build_mcp_service_deps(contract_tmp_path):
    return build_app_runtime_deps(
        config_layers=build_gateway_config_layers(contract_tmp_path),
        thread_root=contract_tmp_path / "threads",
        state_db_path=contract_tmp_path / "gateway.sqlite3",
    )


def close_mcp_service_deps(deps) -> None:
    deps.close()


def mcp_upsert_request(payload: dict) -> McpServerBatchUpsertRequest:
    return McpServerBatchUpsertRequest(config_text=json.dumps(payload))


def home_config_path(contract_tmp_path):
    return contract_tmp_path / ".anvil-home" / "config.yaml"


def project_mcp_config_path(contract_tmp_path):
    return contract_tmp_path / ".anvil" / "mcp.json"


def assert_gateway_error(
    exc: GatewayAdapterError,
    *,
    status_code: int,
    error: str,
    detail_contains: str,
) -> None:
    assert exc.status_code == status_code
    response = exc.to_response()
    assert response.error == error
    assert detail_contains in response.detail


def test_mcp_service_upsert_update_delete_reload_contract(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        add_queue = deps.system_event_bus.subscribe()
        add_payload = asyncio.run(
            services.upsert_mcp_servers(
                deps,
                mcp_upsert_request(
                    {
                        "mcpServers": {
                            "fetch": {
                                "transport": "sse",
                                "inline_tools": [{"name": "fetch_search"}],
                            },
                            "docs": {
                                "transport": "streamable_http",
                                "inline_tools": [{"name": "docs_search"}],
                            },
                            "local": {
                                "transport": "stdio",
                                "inline_tools": [{"name": "local_search"}],
                            },
                        }
                    }
                ),
            )
        )
        add_events = drain_system_events(add_queue)
        deps.system_event_bus.unsubscribe(add_queue)

        assert add_payload.upserted == ["docs", "fetch", "local"]
        assert_mcp_reload_events_match_response(add_events, add_payload.model_dump())
        added_by_id = {item.server_id: item for item in add_payload.servers}
        assert added_by_id["fetch"].transport_kind == "sse"
        assert added_by_id["docs"].transport_kind == "http"
        assert added_by_id["local"].transport_kind == "stdio"
        assert add_payload.reload["scope"] == "all"

        update_queue = deps.system_event_bus.subscribe()
        update_payload = asyncio.run(
            services.upsert_mcp_servers(
                deps,
                mcp_upsert_request(
                    {
                        "mcpServers": {
                            "docs": {
                                "transport": "sse",
                                "inline_tools": [{"name": "docs_sse_search"}],
                            }
                        }
                    }
                ),
            )
        )
        update_events = drain_system_events(update_queue)
        deps.system_event_bus.unsubscribe(update_queue)

        assert update_payload.upserted == ["docs"]
        assert_mcp_reload_events_match_response(update_events, update_payload.model_dump())
        updated_by_id = {item.server_id: item for item in update_payload.servers}
        assert updated_by_id["docs"].transport_kind == "sse"

        delete_queue = deps.system_event_bus.subscribe()
        delete_payload = asyncio.run(services.delete_mcp_server(deps, "fetch"))
        delete_events = drain_system_events(delete_queue)
        deps.system_event_bus.unsubscribe(delete_queue)

        assert delete_payload.deleted is True
        assert delete_payload.server_id == "fetch"
        assert_mcp_reload_events_match_response(delete_events, delete_payload.model_dump())
        remaining_by_id = {item.server_id: item for item in delete_payload.servers}
        assert "fetch" not in remaining_by_id
        assert remaining_by_id["docs"].transport_kind == "sse"
        assert remaining_by_id["local"].transport_kind == "stdio"

        config_path = project_mcp_config_path(contract_tmp_path)
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert sorted(saved["mcpServers"]) == ["docs", "local"]
        assert saved["mcpServers"]["docs"]["transport"] == "sse"
        assert saved["mcpServers"]["docs"]["inline_tools"] == [
            {"name": "docs_sse_search"}
        ]
        assert saved["mcpServers"]["local"]["transport"] == "stdio"

        listed = asyncio.run(services.list_mcp_servers(deps))
        listed_by_id = {item.server_id: item for item in listed}
        assert "fetch" not in listed_by_id
        assert listed_by_id["docs"].transport_kind == "sse"
        assert listed_by_id["local"].transport_kind == "stdio"
        assert listed_by_id["docs"].config_source == str(config_path)
        assert not home_config_path(contract_tmp_path).exists()
    finally:
        close_mcp_service_deps(deps)


@pytest.mark.parametrize(
    ("config_text", "detail_contains"),
    [
        ("", "MCP config cannot be empty"),
        ("[]", "MCP config root must be an object"),
        (json.dumps({"mcpServers": {}}), "MCP config must include at least one"),
        (json.dumps({"mcpServers": {"": {}}}), "MCP server names must not be empty"),
        (json.dumps({"mcpServers": {"bad": []}}), "MCP server 'bad' must be an object"),
        ("{not-json", "invalid JSON"),
    ],
)
def test_mcp_service_rejects_invalid_upsert_config_without_persisting(
    contract_tmp_path,
    monkeypatch,
    config_text,
    detail_contains,
) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        event_queue = deps.system_event_bus.subscribe()
        with pytest.raises(GatewayAdapterError) as raised:
            asyncio.run(
                services.upsert_mcp_servers(
                    deps,
                    McpServerBatchUpsertRequest(config_text=config_text),
                )
            )
        events = drain_system_events(event_queue)
        deps.system_event_bus.unsubscribe(event_queue)

        assert_gateway_error(
            raised.value,
            status_code=400,
            error="invalid_mcp_config",
            detail_contains=detail_contains,
        )
        assert events == []
        assert not project_mcp_config_path(contract_tmp_path).exists()
        assert not home_config_path(contract_tmp_path).exists()
    finally:
        close_mcp_service_deps(deps)


def test_mcp_service_delete_errors_do_not_reload_or_publish_events(
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        event_queue = deps.system_event_bus.subscribe()
        with pytest.raises(GatewayAdapterError) as empty_id:
            asyncio.run(services.delete_mcp_server(deps, "   "))
        assert_gateway_error(
            empty_id.value,
            status_code=400,
            error="invalid_mcp_server",
            detail_contains="cannot be empty",
        )
        assert drain_system_events(event_queue) == []

        with pytest.raises(GatewayAdapterError) as missing:
            asyncio.run(services.delete_mcp_server(deps, "missing"))
        assert_gateway_error(
            missing.value,
            status_code=404,
            error="mcp_server_not_found",
            detail_contains="MCP server 'missing' was not found",
        )
        assert drain_system_events(event_queue) == []
        deps.system_event_bus.unsubscribe(event_queue)
    finally:
        close_mcp_service_deps(deps)


def test_mcp_service_delete_invalid_existing_config_is_conflict(
    contract_tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    config_path = project_mcp_config_path(contract_tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("mcp_servers: [\n", encoding="utf-8")
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        event_queue = deps.system_event_bus.subscribe()
        with pytest.raises(GatewayAdapterError) as raised:
            asyncio.run(services.delete_mcp_server(deps, "docs"))
        events = drain_system_events(event_queue)
        deps.system_event_bus.unsubscribe(event_queue)

        assert_gateway_error(
            raised.value,
            status_code=409,
            error="invalid_mcp_config",
            detail_contains="invalid existing MCP config",
        )
        assert events == []
    finally:
        close_mcp_service_deps(deps)


def test_mcp_admin_reload_rereads_home_profile_config(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    config_path = home_config_path(contract_tmp_path)
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(
            {
                "mcp_servers": {
                    "before": {
                        "enabled": True,
                        "transport": "stdio",
                        "inline_tools": [{"name": "before_tool"}],
                    },
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    deps = build_app_runtime_deps(
        config_layers=build_default_config_layers(repo_root=contract_tmp_path),
        thread_root=contract_tmp_path / "threads",
        state_db_path=contract_tmp_path / "gateway.sqlite3",
    )
    deps.config_coordinator.auto_reload = True
    try:
        before = {item.server_id for item in asyncio.run(services.list_mcp_servers(deps))}
        assert "before" in before

        config_path.write_text(
            yaml.safe_dump(
                {
                    "mcp_servers": {
                        "after": {
                            "enabled": True,
                            "transport": "stdio",
                            "inline_tools": [{"name": "after_tool"}],
                        },
                    },
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )

        payload = asyncio.run(services.admin_reload(deps, scope="mcp"))
        after = {item.server_id for item in asyncio.run(services.list_mcp_servers(deps))}
        assert payload["scope"] == "mcp"
        assert "config_fingerprint" in payload
        assert "before" not in after
        assert "after" in after
    finally:
        close_mcp_service_deps(deps)


def test_mcp_config_overview_counts_visibility(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    monkeypatch.delenv("MISSING_MCP_TOKEN", raising=False)
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        asyncio.run(
            services.upsert_mcp_servers(
                deps,
                mcp_upsert_request(
                    {
                        "mcpServers": {
                            "ready": {
                                "enabled": True,
                                "transport": "stdio",
                                "inline_tools": [{"name": "ready_tool"}],
                            },
                            "needs_key": {
                                "enabled": True,
                                "transport": "stdio",
                                "env": {"API_TOKEN": "$MISSING_MCP_TOKEN"},
                                "inline_tools": [{"name": "hidden_tool"}],
                            },
                            "disabled": {
                                "enabled": False,
                                "transport": "stdio",
                                "inline_tools": [{"name": "disabled_tool"}],
                            },
                        }
                    }
                ),
            )
        )

        overview = asyncio.run(services.get_mcp_config_overview(deps))
        listed = asyncio.run(services.list_mcp_servers(deps))
        listed_by_id = {item.server_id: item for item in listed}

        assert overview.config_path == str(project_mcp_config_path(contract_tmp_path))
        assert overview.server_count == len(listed)
        assert overview.enabled_count == len([item for item in listed if item.enabled])
        assert overview.ready_count == len([item for item in listed if item.ready])
        assert overview.auth_required_count == len([item for item in listed if item.auth_required])
        assert overview.disabled_count == len([item for item in listed if not item.enabled])
        assert overview.failed_count == len([item for item in listed if item.status == "failed"])
        assert overview.hidden_from_model_count == len(
            [
                item
                for item in listed
                if not (item.enabled and item.ready and not item.auth_required and item.status in {"enabled", "ready"})
            ]
        )
        assert listed_by_id["ready"].ready is True
        assert listed_by_id["needs_key"].auth_required is True
        assert listed_by_id["disabled"].enabled is False
    finally:
        close_mcp_service_deps(deps)


def test_mcp_provenance_redacts_secret_values(contract_tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(services, "_runtime_repo_root", lambda: contract_tmp_path)
    deps = build_mcp_service_deps(contract_tmp_path)
    try:
        secret = "real-secret-value"
        asyncio.run(
            services.upsert_mcp_servers(
                deps,
                mcp_upsert_request(
                    {
                        "mcpServers": {
                            "private": {
                                "enabled": True,
                                "type": "http",
                                "url": "https://example.invalid/mcp",
                                "headers": {
                                    "Authorization": f"Bearer {secret}",
                                    "X-Trace": "safe",
                                },
                                "env": {
                                    "API_KEY": secret,
                                    "SAFE_REF": "$MISSING_OPTIONAL_TOKEN",
                                },
                            }
                        }
                    }
                ),
            )
        )

        provenance = asyncio.run(services.get_mcp_server_provenance(deps, "private"))
        payload = json.dumps(provenance.model_dump(mode="json"), ensure_ascii=False)

        assert secret not in payload
        assert "[REDACTED]" in payload
        assert provenance.connection_config["headers"]["X-Trace"] == "safe"
    finally:
        close_mcp_service_deps(deps)
