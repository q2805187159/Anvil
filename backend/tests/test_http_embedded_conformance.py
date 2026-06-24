from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.gateway.services import GatewayAdapterError
from conformance_helpers import normalize_payload, to_json_payload
from fake_models import BindableFakeMessagesListChatModel


def _stable_run_payload(payload):
    return {key: payload[key] for key in ("thread_id", "status", "assistant_message", "last_error", "thread")}


def make_embedded_client(gateway_app, contract_tmp_path: Path):
    from app.sdk import EmbeddedClient, EmbeddedClientConfig

    return EmbeddedClient(
        EmbeddedClientConfig(
            config_layers=gateway_app.state.deps_factory().config_layers,
            feature_set=gateway_app.state.deps_factory().feature_set,
            thread_root=contract_tmp_path / "threads",
            state_db_path=contract_tmp_path / "gateway.sqlite3",
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(content="parity hello"),
                    AIMessage(content="parity hello"),
                ]
            ),
        )
    )


def test_http_and_embedded_surfaces_match_stable_contracts(gateway_app_factory, contract_tmp_path: Path) -> None:
    gateway_app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="parity hello"),
                AIMessage(content="parity hello"),
            ]
        )
    )

    with TestClient(gateway_app) as http_client:
        client = make_embedded_client(gateway_app, contract_tmp_path)
        try:
            thread = client.create_thread(thread_id="shared-thread")
            http_thread = http_client.get("/threads/shared-thread")
            http_state = http_client.get("/threads/shared-thread/state", params={"state_scope": "full"})

            assert normalize_payload(to_json_payload(thread)) == normalize_payload(http_thread.json())
            assert normalize_payload(to_json_payload(client.get_thread_state("shared-thread"))) == normalize_payload(
                http_state.json()
            )

            upload = client.upload_files("shared-thread", [("note.txt", b"hello parity")])
            http_uploads = http_client.get("/threads/shared-thread/uploads")
            assert normalize_payload(to_json_payload(upload)) == normalize_payload(http_uploads.json())

            artifact = http_client.get("/threads/shared-thread/artifacts/uploads/note.txt")
            body, media_type = client.get_artifact_bytes("shared-thread", "uploads", "note.txt")
            assert body == artifact.content
            assert media_type.startswith("text/plain")

            models_http = http_client.get("/models").json()
            skills_http = http_client.get("/skills").json()
            tools_http = http_client.get("/tools/catalog").json()
            memory_http = http_client.get("/memory/stores").json()
            extensions_http = http_client.get("/extensions").json()
            self_upgrade_http = http_client.get("/self-upgrade/health").json()

            assert normalize_payload([to_json_payload(model) for model in client.list_models()]) == normalize_payload(models_http)
            assert normalize_payload([to_json_payload(skill) for skill in client.list_skills()]) == normalize_payload(skills_http)
            assert normalize_payload([to_json_payload(item) for item in client.list_tool_catalog()]) == normalize_payload(tools_http)
            assert normalize_payload([to_json_payload(item) for item in client.list_memory_stores()]) == normalize_payload(memory_http)
            assert normalize_payload([to_json_payload(item) for item in client.list_extensions()]) == normalize_payload(extensions_http)
            assert normalize_payload(to_json_payload(client.get_self_upgrade_health())) == normalize_payload(self_upgrade_http)
        finally:
            client.close()


def test_http_and_embedded_run_views_match_after_normalizing_transport_specific_ids(
    gateway_app_factory,
    contract_tmp_path: Path,
) -> None:
    gateway_app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(content="parity hello"),
                AIMessage(content="parity hello"),
            ]
        )
    )

    with TestClient(gateway_app) as http_client:
        client = make_embedded_client(gateway_app, contract_tmp_path)
        try:
            client.create_thread(thread_id="sdk-thread")
            http_client.post("/threads", json={"thread_id": "http-thread"})

            from app.sdk import EmbeddedRunRequest

            sdk_result = client.run(EmbeddedRunRequest(thread_id="sdk-thread", message="say hello"))
            sdk_state_via_embedded = client.get_thread_state("sdk-thread")
            sdk_state_via_http = http_client.get("/threads/sdk-thread/state", params={"state_scope": "full"})
            assert sdk_state_via_http.status_code == 200

            http_result = http_client.post("/threads/http-thread/runs", json={"message": "say hello"})
            assert http_result.status_code == 200
            http_payload = http_result.json()
            http_state_via_http = http_client.get("/threads/http-thread/state", params={"state_scope": "full"})
            assert http_state_via_http.status_code == 200
            http_state_via_embedded = client.get_thread_state("http-thread")

            sdk_replacements = {"sdk-thread": "<thread_id>"}
            http_replacements = {"http-thread": "<thread_id>"}

            assert sdk_result.status == "completed"
            assert sdk_result.assistant_message == "parity hello"
            assert http_payload["status"] == "completed"
            assert http_payload["assistant_message"] == "parity hello"

            normalized_sdk_run = normalize_payload(
                _stable_run_payload(to_json_payload(sdk_result)),
                replacements=sdk_replacements,
                normalize_runtime_volatiles=True,
            )
            normalized_http_run = normalize_payload(
                _stable_run_payload(http_payload),
                replacements=http_replacements,
                normalize_runtime_volatiles=True,
            )
            assert normalized_http_run == normalized_sdk_run

            assert normalize_payload(
                to_json_payload(sdk_state_via_embedded),
                replacements=sdk_replacements,
                normalize_runtime_volatiles=True,
            ) == normalize_payload(
                sdk_state_via_http.json(),
                replacements=sdk_replacements,
                normalize_runtime_volatiles=True,
            )
            assert normalize_payload(
                to_json_payload(http_state_via_embedded),
                replacements=http_replacements,
                normalize_runtime_volatiles=True,
            ) == normalize_payload(
                http_state_via_http.json(),
                replacements=http_replacements,
                normalize_runtime_volatiles=True,
            )
        finally:
            client.close()


def test_http_and_embedded_error_categories_match_for_missing_thread(
    gateway_app_factory,
    contract_tmp_path: Path,
) -> None:
    gateway_app = gateway_app_factory()
    with TestClient(gateway_app) as http_client:
        client = make_embedded_client(gateway_app, contract_tmp_path)
        try:
            http_response = http_client.post("/threads/missing-thread/runs", json={"message": "hello"})
            assert http_response.status_code == 404
            assert http_response.json()["error"] == "thread_not_found"

            from app.sdk import EmbeddedRunRequest

            try:
                client.run(EmbeddedRunRequest(thread_id="missing-thread", message="hello"))
            except GatewayAdapterError as exc:
                assert exc.status_code == 404
                assert exc.error == "thread_not_found"
            else:
                raise AssertionError("Embedded client did not raise for missing thread")
        finally:
            client.close()
