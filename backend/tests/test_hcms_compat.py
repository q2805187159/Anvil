from __future__ import annotations

import asyncio

from anvil.memory import DebouncedMemoryQueue, FileMemoryStore, HeuristicMemoryUpdater, MemoryService
from anvil.memory.compat import AgentMemoryCompatLayer


def test_agentmemory_compat_observe_consolidate_and_search_roundtrip(contract_tmp_path) -> None:
    service = MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-compat"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(),
    )
    compat = AgentMemoryCompatLayer(service, namespace="global/default")

    observed = asyncio.run(
        compat.observe(
            {
                "content": "User prefers Python for backend automation.",
                "category": "preference",
                "confidence": 0.91,
                "strength": 0.82,
                "sessionId": "thread-compat",
                "tags": ["python", "automation"],
            }
        )
    )
    consolidated = asyncio.run(compat.consolidate("thread-compat"))
    results = asyncio.run(compat.search("Python automation", limit=5))

    assert observed["status"] == "observed"
    assert observed["session_id"] == "thread-compat"
    assert observed["pending_count"] == 1
    assert consolidated["status"] == "consolidated"
    assert consolidated["memories_written"] == 1
    assert consolidated["memory_ids"][0].startswith("mem_")
    assert results[0]["id"] == consolidated["memory_ids"][0]
    assert results[0]["memory_id"] == consolidated["memory_ids"][0]
    assert "Python" in results[0]["content"]
    assert results[0]["score"] > 0


def test_agentmemory_compat_reports_invalid_observations_without_writing(contract_tmp_path) -> None:
    service = MemoryService(
        store=FileMemoryStore(contract_tmp_path / "hcms-compat-invalid"),
        queue=DebouncedMemoryQueue(),
        updater=HeuristicMemoryUpdater(),
    )
    compat = AgentMemoryCompatLayer(service, namespace="global/default")

    observed = asyncio.run(compat.observe({"content": "   ", "sessionId": "thread-invalid"}))
    consolidated = asyncio.run(compat.consolidate("thread-invalid"))

    assert observed["status"] == "error"
    assert "content" in observed["error"]
    assert observed["pending_count"] == 0
    assert consolidated["status"] == "empty"
    assert consolidated["memories_written"] == 0
