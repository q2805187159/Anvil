from __future__ import annotations

import sys

from anvil import (
    ConfigLayer,
    ConfigLayerKind,
    ConfigService,
    PathService,
    ThreadMetadataView,
    ThreadState,
    ToolRegistry,
    create_checkpointer,
    create_store,
)
from anvil.runtime.checkpointers import CheckpointerBackend
from anvil.runtime.store import StoreBackend


def test_phase_three_contract_surface_initializes_without_app(contract_tmp_path) -> None:
    config = ConfigService().resolve(
        [
            ConfigLayer(
                name="default",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "default-model",
                    "models": {"default-model": {"name": "default-model", "provider": "openai"}},
                },
            )
        ]
    )

    path_service = PathService(contract_tmp_path / "threads")
    thread_data = path_service.bootstrap_thread_paths("thread-1")
    state = ThreadState(identity={"thread_id": "thread-1"}, thread_data=thread_data.model_dump())

    checkpointer = create_checkpointer(CheckpointerBackend.IN_MEMORY)
    store = create_store(StoreBackend.IN_MEMORY)
    registry = ToolRegistry()

    checkpointer.put_thread_state(state)
    store.put_thread_metadata(ThreadMetadataView.from_thread_state(state))

    assert config.effective_config.default_model == "default-model"
    assert checkpointer.get_thread_state("thread-1") is not None
    assert store.get_thread_metadata("thread-1") is not None
    assert registry.build_bundle(effective_config_fingerprint=config.fingerprint).visible_tools == ()
    assert "backend.app" not in sys.modules
    assert all(not name.startswith("backend.app.") for name in sys.modules)
